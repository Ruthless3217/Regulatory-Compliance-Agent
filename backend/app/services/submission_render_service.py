"""Single-sided PDF page rendering for the review workspace's document viewer.

Reuses the Compare feature's existing rasterizer (`pdf_render_service.render_pages`)
and positioned-word extractor (`pdf_render_service.positioned_words`) — the same
code `render_orchestrator` uses to build Compare's overlay — instead of building
a second one. There is only one side here (no diff/overlay math): this just gets
page PNGs on disk for whichever PDF-sourced submission is opened for review.

PDF and DOCX. A DOCX is converted through `pdf_render_service.to_pdf` (the
Gotenberg/LibreOffice route) before rasterizing, so the reviewer sees the real
document instead of falling back to the extracted-text pane. Any other
content_type — or a missing/unreadable file — is `page_render_status='skipped'`.

Anchor computation (mapping a violation to a page/bbox) rides along here, in
`violation_anchor_service`: this job is the only place that holds the rendered
PDF, and the geometry it needs is the same `positioned_words` output. It runs
after the pages are on disk and never raises — see `run_render`.
"""
import os
import shutil
import logging
from typing import List, Optional

from app.config import settings
from app.database import SessionLocal
from app.models.submission import Submission
from app.services.pdf_render_service import render_pages, positioned_words, to_pdf, PositionedWord
from app.services.violation_anchor_service import anchor_submission_violations

logger = logging.getLogger(__name__)

# Formats with a page layout `to_pdf` can produce. Anything else is skipped.
RENDERABLE_CONTENT_TYPES = ("pdf", "docx")


def renders_dir(submission_id: str) -> str:
    """Root directory holding a submission's rendered page PNGs."""
    return os.path.join(settings.upload_dir, "renders", "submissions", str(submission_id))


def run_render(submission_id: str) -> None:
    """Background entrypoint: render a submission's PDF pages, persist status.

    Opens its own DB session (never a request-scoped one, matching
    `render_orchestrator.run_render`). Idempotent enough to re-run: the render
    directory is rebuilt each time. Never raises — any failure is recorded as
    `page_render_status='failed'`.
    """
    db = SessionLocal()
    try:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            logger.warning("run_render: submission %s not found", submission_id)
            return

        renderable = (
            submission.content_type in RENDERABLE_CONTENT_TYPES
            and bool(submission.file_path)
            and os.path.exists(submission.file_path)
        )
        if not renderable:
            submission.page_render_status = "skipped"
            db.commit()
            return

        pdf_path = None
        try:
            pdf_path = _render(str(submission.id), submission.file_path, submission.content_type)
            submission.page_render_status = "completed"
        except Exception as e:  # noqa: BLE001 — render must never crash the worker
            logger.error("Page render failed for submission %s: %s", submission_id, e, exc_info=True)
            submission.page_render_status = "failed"
        db.commit()

        # Anchor after the status is committed, so an anchor-side DB failure can
        # never roll the render status back. This anchors whatever findings exist
        # right now, and nothing more: a render kicked off at upload time usually
        # finds none, and an analysis that lands while this job is running writes
        # findings this pass has already gone past. Those stay NULL until the next
        # render (re-upload, or the skipped-render self-heal in GET /submissions),
        # which is a stale box the viewer simply doesn't draw — not a wrong one.
        if pdf_path:
            anchor_submission_violations(db, submission_id, pdf_path)
    finally:
        db.close()


def rendered_pdf_path(submission) -> Optional[str]:
    """The PDF the page images were rasterized from, if it is still on disk.

    A PDF submission is its own upload; a DOCX is the converted copy `_render`
    left in the render directory. Returns None when there is nothing to measure
    against — an unrenderable format, or a render that has not run yet.
    """
    if submission.content_type not in RENDERABLE_CONTENT_TYPES:
        return None
    path = (
        submission.file_path
        if submission.content_type == "pdf"
        else os.path.join(renders_dir(str(submission.id)), "source.pdf")
    )
    return path if path and os.path.exists(path) else None


def anchor_now(db, submission) -> int:
    """Anchor `submission`'s findings against its rendered PDF. Returns the count.

    `run_render` anchors too, but it runs at UPLOAD time, when a submission
    normally has no findings at all — analysis lands minutes later and writes
    violations whose anchor_page/anchor_bbox stay NULL, so the reviewer's page
    view draws no boxes on a document full of findings. This is the pass that
    runs once the findings exist; it is a no-op when there is no rendered PDF.
    """
    pdf_path = rendered_pdf_path(submission)
    if not pdf_path:
        return 0
    return anchor_submission_violations(db, str(submission.id), pdf_path)


def run_anchor(submission_id: str) -> None:
    """Background entrypoint: anchor a submission's findings, once per process.

    Backfill for everything analysed before `anchor_now` was called from the
    analyzer — those findings have NULL anchors and no future event would ever
    give them one.

    ponytail: the "already tried" set is per-process, not a column. A finding
    whose text cannot be located stays NULL by design, so an un-guarded retry
    would re-parse the PDF on every open forever; a restart retrying once is a
    cost worth not paying a migration for.
    """
    if submission_id in _reanchored:
        return
    _reanchored.add(submission_id)
    db = SessionLocal()
    try:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if submission is not None:
            anchor_now(db, submission)
    finally:
        db.close()


_reanchored: set = set()


def _render(submission_id: str, file_path: str, content_type: str) -> str:
    """Rasterize every page of `file_path` into `renders_dir`, returning the PDF
    that was rasterized — the input itself, or the converted copy for a DOCX.

    A non-PDF is converted first; `to_pdf` writes its output inside `base` so
    the rmtree below also disposes of the previous run's converted copy.
    """
    base = renders_dir(submission_id)
    # Rebuild cleanly so a re-run never mixes stale images with fresh ones.
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    pdf_path = to_pdf(file_path, content_type, base, "source")
    render_pages(pdf_path, base, settings.pixel_render_page_cap)
    return pdf_path


def submission_positioned_words(file_path: str) -> List[PositionedWord]:
    """Words-with-bboxes for a submission's PDF (reusing Compare's extractor).

    The anchor pass calls `pdf_render_service.positioned_words` directly (it
    would import this module in a cycle otherwise); this stays as the named
    seam for callers on this side. Raises the same way `positioned_words` does
    for a non-PDF/unreadable path; callers should guard as needed.
    """
    return positioned_words(file_path)
