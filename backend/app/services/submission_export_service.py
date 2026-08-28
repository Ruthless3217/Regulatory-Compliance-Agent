"""Submission export — the 9 downloadable artifacts behind
``GET /submissions/{id}/export/{kind}``.

* ``clean.docx`` / ``clean.pdf`` — the corrected document, generated from the
  working Lexical document (``submissions.lexical_html``). The uploaded file is
  the immutable original and is never edited; a submission with no working
  document falls back to a plain-text rebuild.
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
from app.services.lexical_export import lexical_html_to_docx

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
    """The corrected document.

    Generated from the working Lexical document when one exists — the uploaded
    file is the immutable original and is never edited. Submissions predating
    the editor have no working document and fall back to the plain-text rebuild.

    A DOCX upload is also handed over as the export's template, so the corrected
    document comes back looking like the document it went in as: the corrections
    are written INTO a copy of the upload, paragraph by paragraph, rather than
    the file being rebuilt from the editor's HTML — which would return every
    fixed brochure in default-styled Word. It is read, never written (same guard
    shape as ``lexical_document_service.build_import_html``).
    """
    if not submission.lexical_html:
        return _rebuilt_clean_docx(submission)
    template = (
        submission.file_path
        if submission.content_type == "docx"
        and submission.file_path
        and os.path.exists(submission.file_path)
        else None
    )
    return lexical_html_to_docx(
        submission.lexical_html, submission.title or "Submission", template
    )


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
