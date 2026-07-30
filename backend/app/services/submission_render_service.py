"""Single-sided PDF page rendering for the review workspace's document viewer.

Reuses the Compare feature's existing rasterizer (`pdf_render_service.render_pages`)
and positioned-word extractor (`pdf_render_service.positioned_words`) — the same
code `render_orchestrator` uses to build Compare's overlay — instead of building
a second one. There is only one side here (no diff/overlay math): this just gets
page PNGs on disk for whichever PDF-sourced submission is opened for review.

PDF-only, mirroring `render_orchestrator.run_render`: any other content_type (or
a missing/unreadable file) is `page_render_status='skipped'`.

Anchor computation (mapping a violation to a page/bbox via `positioned_words`) is
deliberately NOT done here — see `preprocessing_service.py`, which flattens PDF
text across pages before chunking with no page boundary kept. Building a
text-to-page mapper as a side effect of this task would be speculative; that's
a followup once something actually needs `violation.anchor_page/anchor_bbox`.
`submission_positioned_words()` below just exposes the reusable extraction for
whenever that followup lands.
"""
import os
import shutil
import logging
from typing import List

from app.config import settings
from app.database import SessionLocal
from app.models.submission import Submission
from app.services.pdf_render_service import render_pages, positioned_words, PositionedWord

logger = logging.getLogger(__name__)


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
            submission.content_type == "pdf"
            and bool(submission.file_path)
            and os.path.exists(submission.file_path)
        )
        if not renderable:
            submission.page_render_status = "skipped"
            db.commit()
            return

        try:
            _render(str(submission.id), submission.file_path)
            submission.page_render_status = "completed"
        except Exception as e:  # noqa: BLE001 — render must never crash the worker
            logger.error("Page render failed for submission %s: %s", submission_id, e, exc_info=True)
            submission.page_render_status = "failed"
        db.commit()
    finally:
        db.close()


def _render(submission_id: str, file_path: str) -> None:
    """Rasterize every page of `file_path` (already a PDF) into `renders_dir`."""
    base = renders_dir(submission_id)
    # Rebuild cleanly so a re-run never mixes stale images with fresh ones.
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    render_pages(file_path, base, settings.pixel_render_page_cap)


def submission_positioned_words(file_path: str) -> List[PositionedWord]:
    """Words-with-bboxes for a submission's PDF (reusing Compare's extractor).

    Not called by `run_render` today — kept as the reusable seam for a future
    anchor pass (see module docstring). Raises the same way `positioned_words`
    does for a non-PDF/unreadable path; callers should guard as needed.
    """
    return positioned_words(file_path)
