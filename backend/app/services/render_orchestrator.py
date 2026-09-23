"""Pixel-faithful render orchestration for the Compare tool.

Wires the previously-unused render pieces together into the overlay model the
frontend expects. Runs as a FastAPI ``BackgroundTask`` after a comparison is
created (or re-run). PDF-only: if either side is not a PDF the render is
``skipped`` and the UI falls back to the text redline. Never raises — any
failure is recorded as ``render_status='failed'`` with ``render_error``.

The emitted ``render_result`` matches ``RenderResult`` in
``frontend/lib/types.ts``:

    {
      "old": {"pages": [{"n", "w_pt", "h_pt", "boxes": [{x0,y0,x1,y1,type,change_id}]}]},
      "new": {"pages": [...]},
      "changes": [{"id", "kind", "old"?: {page,bbox,text}, "new"?: {page,bbox,text}}],
      "truncated_pages": int
    }

Box ``type`` is ``"removed"`` for every old-side box and ``"added"`` for every
new-side box (matching ``RenderBoxType``); the viewer recolors a box violet by
looking up its change's ``kind == "moved"``.
"""
import os
import shutil
import logging
from typing import List, Optional

from app.config import settings
from app.database import SessionLocal
from app.models.document_comparison import DocumentComparison
from app.services.comparison_service import word_level_ops
from app.services.pdf_render_service import (
    to_pdf,
    render_pages,
    positioned_words,
    PositionedWord,
    PageMeta,
)

logger = logging.getLogger(__name__)


def renders_dir(comparison_id: str) -> str:
    """Root directory holding a comparison's rendered page PNGs."""
    return os.path.join(settings.upload_dir, "renders", str(comparison_id))


def run_render(comparison_id: str) -> None:
    """Background entrypoint: render a comparison's pages + overlay, persist it.

    Opens its own DB session (never a request-scoped one). Idempotent enough to
    re-run: the render directory is rebuilt each time.
    """
    db = SessionLocal()
    try:
        c = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
        if not c:
            logger.warning("run_render: comparison %s not found", comparison_id)
            return

        renderable = (
            c.old_content_type in ("pdf", "docx")
            and c.new_content_type in ("pdf", "docx")
            and c.old_file_path
            and c.new_file_path
        )
        if not renderable:
            c.render_status = "skipped"
            c.render_result = None
            c.render_error = None
            db.commit()
            return

        try:
            c.render_result = _render_pair(
                str(c.id),
                c.old_file_path,
                c.new_file_path,
                c.old_content_type,
                c.new_content_type,
            )
            c.render_status = "completed"
            c.render_error = None
        except Exception as e:  # noqa: BLE001 — render must never crash the worker
            logger.error("Render failed for comparison %s: %s", comparison_id, e, exc_info=True)
            c.render_status = "failed"
            c.render_error = str(e)
            c.render_result = None
        db.commit()
    finally:
        db.close()


def _render_pair(
    comparison_id: str,
    old_path: str,
    new_path: str,
    old_content_type: str = "pdf",
    new_content_type: str = "pdf",
) -> dict:
    """Render both sides and assemble the RenderResult overlay model."""
    base = renders_dir(comparison_id)
    # Rebuild cleanly so a re-run never mixes stale images with fresh ones.
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    cap = settings.pixel_render_page_cap

    old_pdf = to_pdf(old_path, old_content_type, os.path.join(base, "old"), "old")
    new_pdf = to_pdf(new_path, new_content_type, os.path.join(base, "new"), "new")

    old_metas, old_trunc = render_pages(old_pdf, os.path.join(base, "old"), cap)
    new_metas, new_trunc = render_pages(new_pdf, os.path.join(base, "new"), cap)

    old_words = positioned_words(old_pdf)
    new_words = positioned_words(new_pdf)

    old_marks, new_marks, changes = word_level_ops(
        [w.text for w in old_words], [w.text for w in new_words]
    )

    return {
        "old": {"pages": _build_pages(old_metas, old_words, old_marks, "removed")},
        "new": {"pages": _build_pages(new_metas, new_words, new_marks, "added")},
        "changes": _build_changes(changes, old_words, old_marks, new_words, new_marks),
        "truncated_pages": old_trunc + new_trunc,
    }


def _build_pages(
    metas: List[PageMeta],
    words: List[PositionedWord],
    marks: List[dict],
    box_type: str,
) -> List[dict]:
    """One RenderPage per rendered page; one box per (change, visual line).

    Words sharing a change_id on the same line (same rounded top) are unioned
    into a single box so a multi-line change highlights each line without
    painting the inter-line gaps.
    """
    # (page, change_id, line_key) -> [x0, y0, x1, y1]
    line_boxes: dict = {}
    for m in marks:
        w = words[m["index"]]
        key = (w.page, m["change_id"], round(w.y0))
        bb = line_boxes.get(key)
        if bb is None:
            line_boxes[key] = [w.x0, w.y0, w.x1, w.y1]
        else:
            bb[0] = min(bb[0], w.x0)
            bb[1] = min(bb[1], w.y0)
            bb[2] = max(bb[2], w.x1)
            bb[3] = max(bb[3], w.y1)

    pages: List[dict] = []
    for meta in metas:
        boxes = [
            {"x0": bb[0], "y0": bb[1], "x1": bb[2], "y1": bb[3], "type": box_type, "change_id": cid}
            for (pn, cid, _line), bb in line_boxes.items()
            if pn == meta.n
        ]
        pages.append({"n": meta.n, "w_pt": meta.w_pt, "h_pt": meta.h_pt, "boxes": boxes})
    return pages


def _first_ref(
    marks: List[dict], words: List[PositionedWord], change_id: str, text: str
) -> Optional[dict]:
    """RenderChangeRef for a change: its first page + union bbox on that page."""
    sel = [words[m["index"]] for m in marks if m["change_id"] == change_id]
    if not sel:
        return None
    page = min(w.page for w in sel)
    on_page = [w for w in sel if w.page == page]
    return {
        "page": page,
        "bbox": [
            min(w.x0 for w in on_page),
            min(w.y0 for w in on_page),
            max(w.x1 for w in on_page),
            max(w.y1 for w in on_page),
        ],
        "text": text,
    }


def _build_changes(
    changes: List[dict],
    old_words: List[PositionedWord],
    old_marks: List[dict],
    new_words: List[PositionedWord],
    new_marks: List[dict],
) -> List[dict]:
    """RenderChange list: each carries an old and/or new ref by its kind."""
    out: List[dict] = []
    for c in changes:
        cid = c["id"]
        entry: dict = {"id": cid, "kind": c["kind"]}
        old_ref = _first_ref(old_marks, old_words, cid, c["old_text"])
        new_ref = _first_ref(new_marks, new_words, cid, c["new_text"])
        if old_ref:
            entry["old"] = old_ref
        if new_ref:
            entry["new"] = new_ref
        out.append(entry)
    return out
