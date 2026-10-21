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
from typing import Callable, List, Optional

from app.config import settings
from app.database import SessionLocal
from app.models.document_comparison import DocumentComparison
from app.services.comparison_service import word_level_ops
from app.services.structural_alignment_service import structural_word_level_ops
from app.services.content_diff_service import (
    compare_furniture,
    compare_words,
    match_segments,
    page_alignment,
    to_blocks,
    to_overlay,
)
from app.services.pdf_render_service import (
    to_pdf,
    render_pages,
    positioned_words_split,
    PositionedWord,
    PageMeta,
)

logger = logging.getLogger(__name__)

# Draftable-style loader: Convert -> Extract -> Compute -> Compare, each 0..100 %.
ProgressFn = Callable[[str, int, str], None]


def _noop_progress(stage: str, pct: int, message: str) -> None:
    return None


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

        def report(stage: str, pct: int, message: str) -> None:
            # Progress rides in render_result while the render is processing; the API
            # exposes it as render_progress. Best-effort: never fails the render.
            try:
                c.render_result = {"progress": {"stage": stage, "percent": int(pct), "message": message}}
                db.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("progress update failed for %s: %s", comparison_id, e)
                db.rollback()

        try:
            render, text_blocks = _render_pair_with_text(
                str(c.id),
                c.old_file_path,
                c.new_file_path,
                c.old_content_type,
                c.new_content_type,
                on_progress=report,
            )
            c.render_result = render
            if text_blocks is not None:
                # one change set for both views: the text redline is rebuilt from the
                # same records as the overlay so they cannot disagree
                c.diff_result = text_blocks
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
    return _render_pair_with_text(comparison_id, old_path, new_path, old_content_type, new_content_type)[0]


def _render_pair_with_text(
    comparison_id: str,
    old_path: str,
    new_path: str,
    old_content_type: str = "pdf",
    new_content_type: str = "pdf",
    on_progress: Optional[ProgressFn] = None,
):
    """(RenderResult, text DiffBlocks or None). The content engine returns the text
    redline built from the same change records; the older engines return None.
    ``on_progress(stage, percent, message)`` drives the viewer's loader."""
    progress = on_progress or _noop_progress
    base = renders_dir(comparison_id)
    # Rebuild cleanly so a re-run never mixes stale images with fresh ones.
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    cap = settings.pixel_render_page_cap

    progress("convert", 0, "Converting the original document")
    old_pdf = to_pdf(old_path, old_content_type, os.path.join(base, "old"), "old")
    progress("convert", 25, "Converting the revised document")
    new_pdf = to_pdf(new_path, new_content_type, os.path.join(base, "new"), "new")

    progress("convert", 50, "Rendering pages of the original")
    old_metas, old_trunc = render_pages(old_pdf, os.path.join(base, "old"), cap)
    progress("convert", 75, "Rendering pages of the revised")
    new_metas, new_trunc = render_pages(new_pdf, os.path.join(base, "new"), cap)
    progress("convert", 100, "Documents converted")

    progress("extract", 0, "Extracting content from the original")
    old_words, old_furn = positioned_words_split(old_pdf)
    progress("extract", 50, "Extracting content from the revised")
    new_words, new_furn = positioned_words_split(new_pdf)
    progress("extract", 100, "Content extracted")

    engine = getattr(settings, "compare_engine", "content")
    text_blocks = None
    page_map = None
    matches = None
    progress("compute", 0, "Aligning the two documents")
    if engine == "content":
        diff = compare_words(old_words, new_words)
        progress("compute", 40, "Classifying changes")
        old_marks, new_marks, changes = to_overlay(diff)
        text_blocks = to_blocks(diff)
        progress("compute", 60, "Aligning pages")
        page_map = page_alignment(diff, len(old_metas), len(new_metas))
        progress("compute", 75, "Linking unchanged text")
        segs = match_segments(diff)
        progress("compute", 90, "Comparing headers and footers")
        # running headers/footers: compared page against page, appended after the body
        # words so every index stays valid on the combined word lists
        fo, fn, fchanges, fsegs = compare_furniture(old_furn, new_furn, page_map)
        oo, no = len(old_words), len(new_words)
        old_marks += [dict(m, index=m["index"] + oo) for m in fo]
        new_marks += [dict(m, index=m["index"] + no) for m in fn]
        for c in fchanges:
            for side, off in (("old_anchor", oo), ("new_anchor", no)):
                if c.get(side):
                    c[side] = (c[side][0] + off, c[side][1])
        changes = changes + fchanges
        segs += [([i + oo for i in o], [j + no for j in n]) for o, n in fsegs]
        old_words = list(old_words) + list(old_furn)
        new_words = list(new_words) + list(new_furn)
        matches = _build_matches(segs, old_words, new_words)
    elif engine == "structural":
        old_marks, new_marks, changes = structural_word_level_ops(old_words, new_words)
    else:
        old_marks, new_marks, changes = word_level_ops(
            [w.text for w in old_words], [w.text for w in new_words]
        )

    progress("compute", 100, "Changes computed")
    progress("compare", 0, "Placing highlights on the pages")
    render = {
        "old": {"pages": _build_pages(old_metas, old_words, old_marks, "removed")},
        "new": {"pages": _build_pages(new_metas, new_words, new_marks, "added")},
        "changes": _build_changes(changes, old_words, old_marks, new_words, new_marks),
        "truncated_pages": old_trunc + new_trunc,
    }
    if page_map is not None:
        render["page_map"] = page_map
        render["changes"] = _document_order(render["changes"], page_map, old_metas, new_metas)
    if matches is not None:
        render["matches"] = matches
    progress("compare", 100, "Comparison ready")
    return render, text_blocks


def _line_rects(words: List[PositionedWord], idx: List[int]) -> List[List[float]]:
    """[page, x0, y0, x1, y1] per visual line (and per same-line run) of the given words."""
    by_page: dict = {}
    for i in idx:
        by_page.setdefault(words[i].page, []).append(words[i])
    rects: List[List[float]] = []
    for page in sorted(by_page):
        for line in _cluster_words_into_lines(by_page[page]):
            for run in _split_line_into_runs(line):
                rects.append([page] + [round(v, 1) for v in (
                    min(w.x0 for w in run), min(w.y0 for w in run),
                    max(w.x1 for w in run), max(w.y1 for w in run))])
    return rects


def _build_matches(segs, old_words: List[PositionedWord], new_words: List[PositionedWord]) -> List[dict]:
    """Matched-text units for hover/click linking: {id, old: [[page,x0,y0,x1,y1]...], new: [...]}."""
    return [{"id": f"m{k}", "old": _line_rects(old_words, o), "new": _line_rects(new_words, n)}
            for k, (o, n) in enumerate(segs)]


def _document_order(changes: List[dict], page_map, old_metas, new_metas) -> List[dict]:
    """Changes in reading order of the aligned sheets: a page's header records first, then
    its body records (already in document order), then its footer records."""
    sheet_of = {("old", o): k for k, (o, n) in enumerate(page_map) if o is not None}
    sheet_of.update({("new", n): k for k, (o, n) in enumerate(page_map) if n is not None})
    heights = {("old", m.n): m.h_pt for m in old_metas}
    heights.update({("new", m.n): m.h_pt for m in new_metas})

    def key(item):
        pos, c = item
        side = "new" if c.get("new") else "old"
        ref = c.get(side)
        if not ref:
            return (len(page_map), 1, pos)
        band = 1
        if c.get("filter_group") == "headers_footers":
            band = 0 if ref["bbox"][1] < heights.get((side, ref["page"]), 1e9) / 2 else 2
        return (sheet_of.get((side, ref["page"]), len(page_map)), band, pos)

    return [c for _, c in sorted(enumerate(changes), key=key)]


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
        for key in ("change_type", "metadata", "structure", "category", "subtype",
                    "filter_group", "default_visible", "move_id"):
            if key in c:
                entry[key] = c[key]

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

        # one-sided change: a caret at the insertion/deletion point on the empty side,
        # so selecting it can scroll both documents
        for side, words in (("old", old_words), ("new", new_words)):
            anchor = c.get(f"{side}_anchor")
            if side not in entry and anchor and 0 <= anchor[0] < len(words):
                w = words[anchor[0]]
                x = w.x1 if anchor[1] == "after" else w.x0
                entry[side] = {"page": w.page, "bbox": [x, w.y0, x + 1.0, w.y1], "text": "",
                               "anchor": True, "locations": []}

        out.append(entry)
    return out
