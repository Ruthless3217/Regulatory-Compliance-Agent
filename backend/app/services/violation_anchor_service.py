"""Map a finding's quoted text onto a page and bbox in the rendered document.

`violations.current_text` is the wording as analysed — extracted, flattened,
whitespace-normalized prose with no page boundary left in it. The reviewer's
document viewer needs the opposite: a page number and a rectangle. This pass
closes that gap by searching the PDF's positioned words (the same extractor
Compare uses) for the quoted phrase and writing `anchor_page`/`anchor_bbox`.

Ambiguity is a miss. If the phrase appears more than once, or not at all, the
anchor stays NULL — the same rule the frontend's `findingAnchor.ts` follows. A
box on the wrong sentence tells a reviewer that compliant text is a violation,
which is worse than no box at all.

Only PDFs (and DOCX already converted to PDF) reach here; anything without page
geometry never gets called and keeps its NULL anchors.
"""
import logging
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.services.pdf_render_service import PositionedWord, positioned_words

logger = logging.getLogger(__name__)

Anchor = Tuple[int, List[float]]


def _normalize(text: str) -> str:
    """Collapse whitespace, fold case — same normalization as
    `lexical_anchor.fingerprint` and the frontend's `normalize()`. The extracted
    text and the positioned words never agree on spacing, so both sides must be
    flattened or every lookup misses."""
    return " ".join((text or "").split()).lower()


def locate(words: Sequence[PositionedWord], text: str) -> Optional[Anchor]:
    """Find `text` among `words`; return (1-based page, [x0, y0, x1, y1]) or None.

    Coordinates are PDF points with a top-left origin, matching `PositionedWord`
    and what `PdfPagePane` scales by RENDER_SCALE.
    """
    needle = _normalize(text)
    if not needle:
        return None

    by_page: Dict[int, List[PositionedWord]] = {}
    for w in words:
        by_page.setdefault(w.page, []).append(w)

    hit: Optional[Anchor] = None
    for page in sorted(by_page):
        # One space-joined string per page, with each token's char offset kept so
        # a match can be walked back to the words it covers.
        tokens = [(_normalize(w.text), w) for w in by_page[page]]
        tokens = [tw for tw in tokens if tw[0]]
        if not tokens:
            continue
        starts: List[int] = []
        pos = 0
        for tok, _ in tokens:
            starts.append(pos)
            pos += len(tok) + 1
        haystack = " ".join(tok for tok, _ in tokens)
        token_at = {s: i for i, s in enumerate(starts)}

        frm = 0
        while True:
            at = haystack.find(needle, frm)
            if at == -1:
                break
            frm = at + 1
            end = at + len(needle)
            # Both ends must land on token boundaries: "guaranteed returns" is a
            # substring of "unguaranteed returns" but not the same claim.
            if at not in token_at or (end != len(haystack) and haystack[end] != " "):
                continue
            first = token_at[at]
            last = first
            while last + 1 < len(tokens) and starts[last + 1] < end:
                last += 1
            matched = [w for _, w in tokens[first:last + 1]]

            # A phrase that wraps produces words on two lines. Union them all and
            # the box swallows everything between the first word and the last —
            # the right margin, the left margin, and whatever sits in between. So
            # box only the FIRST line of the match: a partial highlight that is
            # true beats a full one that is wrong. Line identity is the rounded
            # top, the same key `positioned_words` groups visual lines by.
            top = round(matched[0].y0)
            line = [w for w in matched if round(w.y0) == top]
            box = [
                min(w.x0 for w in line),
                min(w.y0 for w in line),
                max(w.x1 for w in line),
                max(w.y1 for w in line),
            ]
            if hit is not None:
                return None  # second occurrence — ambiguous, so no anchor
            hit = (page, box)
    return hit


def anchor_submission_violations(db, submission_id: str, pdf_path: str) -> int:
    """Anchor every violation of `submission_id` against `pdf_path`. Returns the
    number anchored.

    Never raises: this runs inside the page-render background job, and a failed
    anchor pass must not fail the render or the analysis. Commits its own work
    (and rolls back on error) so the caller's session is left usable.
    """
    try:
        words = positioned_words(pdf_path)
        violations = (
            db.query(Violation)
            .join(ComplianceCheck, Violation.compliance_check_id == ComplianceCheck.id)
            .filter(ComplianceCheck.submission_id == submission_id)
            .all()
        )
        anchored = 0
        for v in violations:
            found = locate(words, v.current_text or "")
            # Assigned either way: this pass owns both columns, so a finding whose
            # text no longer resolves loses its stale box instead of keeping it.
            v.anchor_page, v.anchor_bbox = found if found else (None, None)
            anchored += 1 if found else 0
        db.commit()
        return anchored
    except Exception as e:  # noqa: BLE001 — an anchor failure is never fatal
        logger.warning("Anchor pass failed for submission %s: %s", submission_id, e, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
