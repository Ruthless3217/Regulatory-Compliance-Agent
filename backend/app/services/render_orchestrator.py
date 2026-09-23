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


def _cluster_words_into_lines(words: List[PositionedWord]) -> List[List[PositionedWord]]:
    """Group words on a page into visual text lines using vertical overlap."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w.y0, w.x0))
    lines: List[List[PositionedWord]] = []

    for w in sorted_words:
        placed = False
        for line in lines:
            line_y0 = min(lw.y0 for lw in line)
            line_y1 = max(lw.y1 for lw in line)
            overlap = min(w.y1, line_y1) - max(w.y0, line_y0)
            min_h = min(w.y1 - w.y0, line_y1 - line_y0)
            if (min_h > 0 and overlap >= 0.4 * min_h) or (
                abs(w.y0 - line_y0) <= 3.5 and abs(w.y1 - line_y1) <= 3.5
            ):
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])

    lines.sort(key=lambda line: sum(w.y0 for w in line) / len(line))
    return lines


def _split_line_into_runs(
    line_words: List[PositionedWord], max_gap: float = 16.0
) -> List[List[PositionedWord]]:
    """Split words on the same visual line into contiguous runs if there are horizontal gaps.

    Ensures distant words on the same line are not bridged across unchanged text.
    """
    if not line_words:
        return []
    sorted_line = sorted(line_words, key=lambda w: w.x0)
    runs: List[List[PositionedWord]] = []
    current_run: List[PositionedWord] = [sorted_line[0]]

    for next_w in sorted_line[1:]:
        prev_w = current_run[-1]
        gap = next_w.x0 - prev_w.x1
        h = max(prev_w.y1 - prev_w.y0, next_w.y1 - next_w.y0, 10.0)
        gap_threshold = max(max_gap, 1.4 * h)
        if gap > gap_threshold:
            runs.append(current_run)
            current_run = [next_w]
        else:
            current_run.append(next_w)
    if current_run:
        runs.append(current_run)
    return runs


def _generate_box_records(
    words: List[PositionedWord], marks: List[dict], box_type: str
) -> List[dict]:
    """Generate precise bounding box records for marked words across pages and lines."""
    # Group by (page, change_id)
    by_page_change: dict = {}
    for m in marks:
        w = words[m["index"]]
        key = (w.page, m["change_id"])
        by_page_change.setdefault(key, []).append(w)

    records: List[dict] = []
    # Sort keys by page then change_id for consistent ordering
    for (page, cid), group_words in sorted(by_page_change.items(), key=lambda x: (x[0][0], x[0][1])):
        lines = _cluster_words_into_lines(group_words)
        box_idx = 0
        for line in lines:
            runs = _split_line_into_runs(line)
            for run in runs:
                box_id = f"{cid}-p{page}-b{box_idx}"
                box_idx += 1
                records.append({
                    "page": page,
                    "change_id": cid,
                    "box_id": box_id,
                    "x0": min(w.x0 for w in run),
                    "y0": min(w.y0 for w in run),
                    "x1": max(w.x1 for w in run),
                    "y1": max(w.y1 for w in run),
                    "text": " ".join(w.text for w in run),
                    "type": box_type,
                })
    return records


def _build_pages(
    metas: List[PageMeta],
    words: List[PositionedWord],
    marks: List[dict],
    box_type: str,
) -> List[dict]:
    """One RenderPage per rendered page with precise bounding boxes."""
    records = _generate_box_records(words, marks, box_type)
    pages: List[dict] = []
    for meta in metas:
        boxes = [
            {
                "box_id": r["box_id"],
                "x0": r["x0"],
                "y0": r["y0"],
                "x1": r["x1"],
                "y1": r["y1"],
                "type": r["type"],
                "change_id": r["change_id"],
            }
            for r in records
            if r["page"] == meta.n
        ]
        pages.append({"n": meta.n, "w_pt": meta.w_pt, "h_pt": meta.h_pt, "boxes": boxes})
    return pages


def _build_changes(
    changes: List[dict],
    old_words: List[PositionedWord],
    old_marks: List[dict],
    new_words: List[PositionedWord],
    new_marks: List[dict],
) -> List[dict]:
    """RenderChange list: each carries old and/or new refs with full location lists."""
    old_records = _generate_box_records(old_words, old_marks, "removed")
    new_records = _generate_box_records(new_words, new_marks, "added")

    old_by_cid: dict = {}
    for r in old_records:
        old_by_cid.setdefault(r["change_id"], []).append(r)

    new_by_cid: dict = {}
    for r in new_records:
        new_by_cid.setdefault(r["change_id"], []).append(r)

    out: List[dict] = []
    for c in changes:
        cid = c["id"]
        entry: dict = {"id": cid, "kind": c["kind"]}

        old_locs = old_by_cid.get(cid, [])
        if old_locs:
            first_loc = old_locs[0]
            entry["old"] = {
                "page": first_loc["page"],
                "bbox": [first_loc["x0"], first_loc["y0"], first_loc["x1"], first_loc["y1"]],
                "text": c["old_text"],
                "locations": [
                    {
                        "page": loc["page"],
                        "bbox": [loc["x0"], loc["y0"], loc["x1"], loc["y1"]],
                        "text": loc["text"],
                        "box_id": loc["box_id"],
                    }
                    for loc in old_locs
                ],
            }

        new_locs = new_by_cid.get(cid, [])
        if new_locs:
            first_loc = new_locs[0]
            entry["new"] = {
                "page": first_loc["page"],
                "bbox": [first_loc["x0"], first_loc["y0"], first_loc["x1"], first_loc["y1"]],
                "text": c["new_text"],
                "locations": [
                    {
                        "page": loc["page"],
                        "bbox": [loc["x0"], loc["y0"], loc["x1"], loc["y1"]],
                        "text": loc["text"],
                        "box_id": loc["box_id"],
                    }
                    for loc in new_locs
                ],
            }

        out.append(entry)
    return out
