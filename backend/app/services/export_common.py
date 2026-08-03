"""Shared helpers for submission export (``GET /submissions/{id}/export/{kind}``).

Three things live here so ``submission_export_service`` stays a thin set of
DOCX builders:

* Pulling the report data (latest ``ComplianceCheck``/``Violation`` rows,
  every ``rule_feedback`` row for the submission) — the same "latest check
  for a submission" query ``compliance.py``'s ``GET /results/{id}`` already
  uses.
* ``find_spans`` — a Python port of ``frontend/lib/highlightMarkup.ts``'s
  ``findSpans``/``resolveOverlaps`` (same whitespace/case normalization the
  backend's own evidence-grounding check uses), so the annotated export
  highlights exactly the text the reviewer document viewer highlights.
* ``docx_bytes_to_pdf`` — the DOCX->PDF conversion, reusing
  ``pdf_render_service.to_pdf``'s Gotenberg seam rather than a second
  ``gotenberg_client`` call site.
"""
import os
import re
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.compliance_check import ComplianceCheck
from app.models.rule_feedback import RuleFeedback
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.violation import Violation
from app.services.pdf_render_service import to_pdf

# 4-tier severity scale (matches frontend/lib/format.ts::normalizeSeverity) —
# "moderate"/"informational"/"info" are precedent-path aliases; anything
# unrecognized defaults to medium so it stays visible rather than vanishing.
_SEVERITY_ALIASES = {"moderate": "medium", "informational": "low", "info": "low"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def normalize_severity(severity: Optional[str]) -> str:
    s = (severity or "").strip().lower()
    s = _SEVERITY_ALIASES.get(s, s)
    return s if s in SEVERITY_ORDER else "medium"


def latest_check(db: Session, submission_id) -> Optional[ComplianceCheck]:
    """Most recent ComplianceCheck for a submission, or None if never analyzed.
    Same query as compliance.py's GET /results/{submission_id}."""
    return (
        db.query(ComplianceCheck)
        .filter(ComplianceCheck.submission_id == submission_id)
        .order_by(ComplianceCheck.checked_at.desc())
        .first()
    )


def findings_are_stale(db: Session, submission_id) -> bool:
    """True when the document was edited after the newest analysis ran.

    Derived from the two timestamps rather than stored: a `stale` column would
    need every edit path (manual edit, apply_fix, bulk_apply_fixes, restore) to
    remember to set it, and any that forgot would ship findings that silently
    describe a superseded document.

    A submission that was never analysed is not stale — there is nothing to
    contradict. Rows predating migration 0026 can carry a NULL timestamp on
    either side; treat that as not-stale rather than blocking every legacy
    export.
    """
    check = latest_check(db, submission_id)
    if check is None or check.checked_at is None:
        return False
    newest_edit = (
        db.query(SubmissionRevision.created_at)
        .filter(SubmissionRevision.submission_id == submission_id)
        .order_by(SubmissionRevision.created_at.desc())
        .limit(1)
        .scalar()
    )
    if newest_edit is None:
        return False
    return newest_edit > check.checked_at


def check_violations(db: Session, check: Optional[ComplianceCheck]) -> List[Violation]:
    """All violations of one check, in finding order. Empty if never analyzed."""
    if check is None:
        return []
    return (
        db.query(Violation)
        .filter(Violation.compliance_check_id == check.id)
        .order_by(Violation.created_at)
        .all()
    )


def submission_feedback(db: Session, submission_id) -> List[RuleFeedback]:
    """Every reviewer action recorded against this submission (0023's
    rule_feedback.submission_id column), oldest first."""
    return (
        db.query(RuleFeedback)
        .filter(RuleFeedback.submission_id == submission_id)
        .order_by(RuleFeedback.created_at)
        .all()
    )


def document_text(submission: Submission) -> str:
    """The current working text: edited content if any, else the original.
    Both can legitimately be empty for a file upload that has never been
    preprocessed yet (original_content is backfilled by
    preprocessing_service on first analysis)."""
    return submission.current_content or submission.original_content or ""


# --- Violation highlight spans (port of highlightMarkup.ts) -----------------

@dataclass
class HighlightSpan:
    start: int
    end: int
    severity: str
    violation: Violation


def _normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """Lowercase + collapse whitespace runs to single spaces, keeping a map
    from each normalized character back to its original offset — mirrors
    highlightMarkup.ts::normalizeWithMap exactly, so a match here lands on
    the real (original-cased) text."""
    norm_chars: List[str] = []
    idx_map: List[int] = []
    prev_was_space = True  # skip leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_was_space:
                norm_chars.append(" ")
                idx_map.append(i)
                prev_was_space = True
        else:
            norm_chars.append(ch.lower())
            idx_map.append(i)
            prev_was_space = False
    return "".join(norm_chars), idx_map


def _normalize_needle(s: str) -> str:
    return " ".join(s.strip().lower().split())


def find_spans(text: str, violations: List[Violation]) -> List[HighlightSpan]:
    """Every occurrence of each violation's current_text in `text`, overlaps
    resolved by highest severity then longest span — same algorithm as the
    reviewer document viewer, so the annotated export marks exactly what a
    reviewer sees on screen."""
    norm, idx_map = _normalize_with_map(text)
    spans: List[HighlightSpan] = []
    for v in violations:
        needle = _normalize_needle(v.current_text or "")
        if not needle:
            continue
        start_from = 0
        while start_from <= len(norm) - len(needle):
            found = norm.find(needle, start_from)
            if found == -1:
                break
            spans.append(
                HighlightSpan(
                    start=idx_map[found],
                    end=idx_map[found + len(needle) - 1] + 1,
                    severity=normalize_severity(v.severity),
                    violation=v,
                )
            )
            start_from = found + len(needle)
    return _resolve_overlaps(spans)


def _resolve_overlaps(spans: List[HighlightSpan]) -> List[HighlightSpan]:
    ordered = sorted(
        spans, key=lambda s: (s.start, SEVERITY_ORDER[s.severity], -(s.end - s.start))
    )
    accepted: List[HighlightSpan] = []
    for s in ordered:
        if not accepted or s.start >= accepted[-1].end:
            accepted.append(s)
            continue
        last = accepted[-1]
        cur_rank, prev_rank = SEVERITY_ORDER[s.severity], SEVERITY_ORDER[last.severity]
        if cur_rank < prev_rank or (
            cur_rank == prev_rank and (s.end - s.start) > (last.end - last.start)
        ):
            accepted[-1] = s
    return sorted(accepted, key=lambda s: s.start)


def iter_paragraphs(text: str) -> List[Tuple[int, int]]:
    """(start, end) offsets of each paragraph, splitting on runs of blank
    lines — same boundary highlightMarkup.ts::applyHighlightsAsParagraphs
    uses (``text.split(/\\n\\n+/)``)."""
    bounds: List[Tuple[int, int]] = []
    pos = 0
    for m in re.finditer(r"\n\n+", text):
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(text)))
    return bounds


# --- DOCX -> PDF (reuses pdf_render_service.to_pdf's Gotenberg seam) --------

def docx_bytes_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert generated DOCX bytes to PDF via the Gotenberg sidecar, reusing
    ``pdf_render_service.to_pdf``'s DOCX-conversion seam instead of a second
    ``gotenberg_client`` call site. Raises ``GotenbergError`` (propagated from
    ``to_pdf``) if the sidecar is unreachable."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "export.docx")
        with open(src, "wb") as f:
            f.write(docx_bytes)
        pdf_path = to_pdf(src, "docx", tmp, "export")
        with open(pdf_path, "rb") as f:
            return f.read()
