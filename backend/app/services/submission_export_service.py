"""Submission export — the 9 downloadable artifacts behind
``GET /submissions/{id}/export/{kind}``.

* ``clean.docx`` / ``clean.pdf`` — the corrected document. For a DOCX upload
  this is the original file with accepted edits written into it, so formatting
  survives; only a non-DOCX source falls back to a plain-text rebuild.
* ``annotated.docx`` / ``annotated.pdf`` — the current text with violation
  highlights (same spans ``export_common.find_spans`` computes for the
  reviewer document viewer) plus a numbered findings table.
* ``report.docx`` / ``report.pdf`` — the latest analysis run's findings, as
  a table.
* ``feedback-report.docx`` / ``feedback-report.pdf`` — every reviewer action
  recorded on this submission (``rule_feedback``), as a table.
* ``bundle.zip`` — all eight files, zipped (best-effort: a PDF conversion
  failure drops just that one file rather than the whole bundle, matching
  ``export_service.bundle_zip``'s behaviour for Compare exports).

Every ``*.pdf`` kind is produced by building the ``*.docx`` first and running
it through ``export_common.docx_bytes_to_pdf`` (which itself reuses
``pdf_render_service.to_pdf``'s Gotenberg seam) — one conversion path, reused
four times, instead of a second HTML-rendering pipeline.
"""
import io
import logging
import os
import zipfile
from datetime import datetime, timezone
from typing import List, Optional

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from sqlalchemy.orm import Session

from app.models.compliance_check import ComplianceCheck
from app.models.rule_feedback import RuleFeedback
from app.models.submission import Submission
from app.models.violation import Violation
from app.services import export_common as ec

logger = logging.getLogger(__name__)

_HIGHLIGHT_COLOR = {
    "critical": WD_COLOR_INDEX.RED,
    "high": WD_COLOR_INDEX.YELLOW,
    "medium": WD_COLOR_INDEX.TURQUOISE,
    "low": WD_COLOR_INDEX.GRAY_25,
}

_NO_CONTENT_NOTICE = "No document content available (not yet analyzed, or an empty upload)."


def _add_broken_text(paragraph, text: str, highlight=None) -> None:
    """Add `text` to `paragraph`, turning single newlines into line breaks —
    python-docx runs have no native multi-line text."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i > 0:
            paragraph.add_run().add_break()
        if not line:
            continue
        run = paragraph.add_run(line)
        if highlight is not None:
            run.font.highlight_color = highlight


def _header(doc: Document, title: str, subtitle: str = "") -> None:
    doc.add_heading(title, level=0)
    meta = doc.add_paragraph()
    meta.add_run(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC").italic = True
    if subtitle:
        doc.add_paragraph(subtitle)


def _edited_original_docx(submission: Submission) -> Optional[bytes]:
    """The uploaded DOCX with accepted edits applied, or None if not possible.

    Rebuilding the document from extracted text (``_rebuilt_clean_docx`` below)
    discards fonts, tables, images, headers, and page structure — everything the
    reviewer is asked to preserve. So when the upload really is a DOCX, edit
    that file instead of regenerating one.

    Paragraphs are aligned by text against the extraction the reviewer edited.
    Untouched paragraphs are not written to at all, so anything python-docx
    cannot model (tables, images, headers/footers, section breaks) survives
    byte-for-byte. Returns None when the alignment is not trustworthy, and the
    caller falls back to the rebuild rather than emitting a mangled document.
    """
    if submission.content_type != "docx" or not submission.file_path:
        return None
    if not os.path.exists(submission.file_path):
        logger.warning("clean.docx: upload missing for %s, rebuilding", submission.id)
        return None

    current = ec.document_text(submission)
    if not current:
        return None
    original = submission.original_content or ""
    if current == original:
        # Never edited — the upload already is the answer.
        with open(submission.file_path, "rb") as f:
            return f.read()

    try:
        doc = Document(submission.file_path)
    except Exception as e:  # noqa: BLE001 — a corrupt upload must not fail the export
        logger.warning("clean.docx: cannot open upload for %s (%s), rebuilding", submission.id, e)
        return None

    old_paras = [original[s:e].strip() for s, e in ec.iter_paragraphs(original)]
    new_paras = [current[s:e].strip() for s, e in ec.iter_paragraphs(current)]
    # Only paragraph-for-paragraph rewrites can be mapped back onto runs;
    # an edit that adds or removes whole paragraphs has no anchor in the
    # original file, so hand those to the rebuild.
    if len(old_paras) != len(new_paras):
        return None

    body = [p for p in doc.paragraphs if p.text.strip()]
    changed = {i for i, (o, n) in enumerate(zip(old_paras, new_paras)) if o != n}
    by_text: dict[str, list] = {}
    for para in body:
        by_text.setdefault(para.text.strip(), []).append(para)

    for i in sorted(changed):
        targets = by_text.get(old_paras[i]) or []
        if len(targets) != 1:
            # Absent, or ambiguous because the same text repeats — writing to
            # the wrong paragraph is worse than falling back.
            return None
        _replace_paragraph_text(targets[0], new_paras[i])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _replace_paragraph_text(paragraph, text: str) -> None:
    """Set a paragraph's text, keeping its style and first run's formatting.

    ponytail: run-level formatting *within* an edited paragraph collapses to
    the first run's (bold/italic spanning part of the sentence is lost for that
    paragraph only). Unedited paragraphs are never touched. Upgrade path is a
    character-offset diff mapped onto runs, if reviewers report losing
    mid-sentence emphasis on text they corrected.
    """
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run.text = ""


def _rebuilt_clean_docx(submission: Submission) -> bytes:
    """Plain-text reflow. Only for uploads with no DOCX to preserve."""
    doc = Document()
    _header(doc, submission.title or "Submission")
    text = ec.document_text(submission)
    if not text:
        doc.add_paragraph(_NO_CONTENT_NOTICE)
    for start, end in ec.iter_paragraphs(text):
        _add_broken_text(doc.add_paragraph(), text[start:end])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _clean_docx(submission: Submission) -> bytes:
    return _edited_original_docx(submission) or _rebuilt_clean_docx(submission)


def _annotated_docx(submission: Submission, violations: List[Violation]) -> bytes:
    text = ec.document_text(submission)
    spans = ec.find_spans(text, violations) if text else []

    doc = Document()
    _header(doc, submission.title or "Submission", "Annotated — highlights mark flagged text")

    if not text:
        doc.add_paragraph(_NO_CONTENT_NOTICE)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    # Number violations in first-appearance order (matches export_service's
    # _numbered_annotations badge scheme for the Compare PDF export).
    number_by_violation: dict = {}
    for s in spans:
        number_by_violation.setdefault(str(s.violation.id), len(number_by_violation) + 1)

    for p_start, p_end in ec.iter_paragraphs(text):
        p_spans = [s for s in spans if s.start < p_end and s.end > p_start]
        paragraph = doc.add_paragraph()
        cursor = p_start
        for s in p_spans:
            s_start, s_end = max(s.start, p_start), min(s.end, p_end)
            if s_start > cursor:
                _add_broken_text(paragraph, text[cursor:s_start])
            _add_broken_text(
                paragraph,
                text[s_start:s_end],
                highlight=_HIGHLIGHT_COLOR.get(s.severity, WD_COLOR_INDEX.YELLOW),
            )
            n = number_by_violation[str(s.violation.id)]
            paragraph.add_run(f" [{n}]").italic = True
            cursor = s_end
        if cursor < p_end:
            _add_broken_text(paragraph, text[cursor:p_end])

    if number_by_violation:
        doc.add_heading("Findings", level=1)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, head in zip(table.rows[0].cells, ["#", "Severity", "Category", "Description"]):
            cell.text = head
        by_id = {str(s.violation.id): s.violation for s in spans}
        for vid, n in sorted(number_by_violation.items(), key=lambda kv: kv[1]):
            v = by_id[vid]
            cells = table.add_row().cells
            cells[0].text = str(n)
            cells[1].text = ec.normalize_severity(v.severity)
            cells[2].text = v.category or ""
            cells[3].text = v.description or ""

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _report_docx(
    submission: Submission, check: Optional[ComplianceCheck], violations: List[Violation]
) -> bytes:
    doc = Document()
    if check is not None:
        subtitle = (
            f"Score {check.overall_score if check.overall_score is not None else '—'} · "
            f"Grade {check.grade or '—'} · {len(violations)} finding(s)"
        )
    else:
        subtitle = "No analysis run yet"
    _header(doc, submission.title or "Submission", subtitle)

    table = doc.add_table(rows=1, cols=7)
    table.style = "Table Grid"
    headers = ["#", "Severity", "Category", "Flagged text", "Description", "Suggested fix", "Suppressed"]
    for cell, head in zip(table.rows[0].cells, headers):
        cell.text = head
    for idx, v in enumerate(violations, 1):
        cells = table.add_row().cells
        cells[0].text = str(idx)
        cells[1].text = ec.normalize_severity(v.severity)
        cells[2].text = v.category or ""
        cells[3].text = v.current_text or ""
        cells[4].text = v.description or ""
        cells[5].text = v.suggested_fix or ""
        cells[6].text = "Yes" if v.suppressed else "No"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _feedback_report_docx(submission: Submission, feedback: List[RuleFeedback]) -> bytes:
    doc = Document()
    _header(doc, submission.title or "Submission", f"{len(feedback)} reviewer action(s)")

    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    headers = ["#", "Verdict", "Reason", "Flagged text", "Final text", "Comment"]
    for cell, head in zip(table.rows[0].cells, headers):
        cell.text = head
    for idx, fb in enumerate(feedback, 1):
        cells = table.add_row().cells
        cells[0].text = str(idx)
        cells[1].text = fb.verdict or ""
        cells[2].text = fb.reason or ""
        cells[3].text = fb.original_text or ""
        cells[4].text = fb.final_text or ""
        cells[5].text = fb.comment or ""

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_export(db: Session, submission: Submission, kind: str) -> bytes:
    """Build one export artifact.

    Raises ``ValueError`` for an unknown kind (the route maps that to 404 —
    it also pre-checks kind against its own allow-list before ever calling
    here). Lets Gotenberg's ``GotenbergError`` propagate for a ``*.pdf`` kind
    if the sidecar is unreachable (the route maps that to 502).
    """
    check = ec.latest_check(db, submission.id)
    violations = ec.check_violations(db, check)
    feedback = ec.submission_feedback(db, submission.id)

    builders = {
        "clean": lambda: _clean_docx(submission),
        "annotated": lambda: _annotated_docx(submission, violations),
        "report": lambda: _report_docx(submission, check, violations),
        "feedback-report": lambda: _feedback_report_docx(submission, feedback),
    }

    if kind == "bundle.zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, build in builders.items():
                docx_bytes = build()
                z.writestr(f"{name}.docx", docx_bytes)
                try:
                    z.writestr(f"{name}.pdf", ec.docx_bytes_to_pdf(docx_bytes))
                except Exception as e:  # noqa: BLE001 — bundle still ships the docx set
                    logger.warning("bundle_zip: skipping %s.pdf for %s: %s", name, submission.id, e)
        return buf.getvalue()

    base, _, ext = kind.rpartition(".")
    if base not in builders or ext not in ("docx", "pdf"):
        raise ValueError(f"Unknown export kind: {kind!r}")

    docx_bytes = builders[base]()
    if ext == "docx":
        return docx_bytes
    return ec.docx_bytes_to_pdf(docx_bytes)
