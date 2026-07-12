"""Compare export artifacts — no new containers, no LLM.

Produces the downloadable outputs the desktop "Copy to Clipboard / Email" pane
offers:

* ``changes-report.docx`` — the change list as a Word table (works for every
  comparison, including text-only).
* ``old-highlighted.pdf`` / ``new-highlighted.pdf`` — the rendered pages with the
  overlay boxes burned in (needs a completed render).
* ``side-by-side.pdf`` — old and new pages composited per sheet (needs a render).
* ``bundle.zip`` — the report + any available PDFs + the original inputs.

PDF artifacts reuse the PNGs written by ``render_orchestrator``; boxes are scaled
from PDF points to image pixels per page.
"""
import io
import os
import zipfile
import logging
from datetime import datetime, timezone
from typing import List, Optional

from PIL import Image, ImageDraw
import img2pdf
from docx import Document

from app.services.render_orchestrator import renders_dir

logger = logging.getLogger(__name__)

_KIND_LABEL = {"removed": "Removed", "added": "Added", "modified": "Modified", "moved": "Moved"}

# RGBA fills / outlines for page-overlay burn-in (match the viewer palette).
_FILL = {
    "removed": (220, 38, 38, 90),   # sev-critical red
    "added": (22, 163, 74, 90),     # success green
    "moved": (124, 58, 237, 90),    # violet-600
}
_OUTLINE = {
    "removed": (220, 38, 38, 255),
    "added": (22, 163, 74, 255),
    "moved": (124, 58, 237, 255),
}


def _page_image_path(comparison_id: str, side: str, n: int) -> str:
    return os.path.join(renders_dir(comparison_id), side, f"page-{n:04d}.png")


def _open_page(comparison_id: str, side: str, n: int) -> Optional[Image.Image]:
    path = _page_image_path(comparison_id, side, n)
    return Image.open(path) if os.path.exists(path) else None


def derive_changes(comparison) -> List[dict]:
    """Normalize either render_result.changes or diff blocks into report rows.

    Returns ``[{change_id, kind, old_text, new_text}]`` in document order. Uses
    the pixel changes when a render completed, else the text-diff blocks (with
    the same ``b{index}`` change ids the viewer assigns).
    """
    rr = comparison.render_result
    if comparison.render_status == "completed" and rr and rr.get("changes"):
        return [
            {
                "change_id": c["id"],
                "kind": c["kind"],
                "old_text": (c.get("old") or {}).get("text", ""),
                "new_text": (c.get("new") or {}).get("text", ""),
            }
            for c in rr["changes"]
        ]

    out: List[dict] = []
    for i, b in enumerate(comparison.diff_result or []):
        t = b.get("type")
        if t == "equal":
            continue
        kind = "moved" if b.get("moved") else {"delete": "removed", "insert": "added"}.get(t, "modified")
        if t == "delete":
            old_text, new_text = b.get("old_text", ""), ""
        elif t == "insert":
            old_text, new_text = "", b.get("new_text", "")
        elif t == "replace":
            old_text = " ".join(w["text"] for w in b.get("old_words", []) if w.get("changed"))
            new_text = " ".join(w["text"] for w in b.get("new_words", []) if w.get("changed"))
        else:
            old_text = new_text = ""
        out.append({"change_id": f"b{i}", "kind": kind, "old_text": old_text, "new_text": new_text})
    return out


def changes_report_docx(comparison, annotations) -> bytes:
    """The change list as a Word document (n° / type / original / revised / note / tags)."""
    ann_by_change = {a.change_id: a for a in annotations}
    changes = derive_changes(comparison)

    doc = Document()
    doc.add_heading(comparison.title or "Document comparison", level=0)

    created = comparison.created_at.strftime("%Y-%m-%d %H:%M") if comparison.created_at else "—"
    meta = doc.add_paragraph()
    meta.add_run(
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · created {created}"
    ).italic = True

    counts: dict = {}
    for c in changes:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    summary = doc.add_paragraph()
    summary.add_run("Summary: ").bold = True
    summary.add_run(
        ", ".join(f"{_KIND_LABEL.get(k, k)}: {counts[k]}" for k in sorted(counts)) or "No changes"
    )

    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    for cell, head in zip(table.rows[0].cells, ["#", "Type", "Original", "Revised", "Note", "Tags"]):
        cell.text = head
    for idx, c in enumerate(changes, 1):
        a = ann_by_change.get(c["change_id"])
        cells = table.add_row().cells
        cells[0].text = str(idx)
        cells[1].text = _KIND_LABEL.get(c["kind"], c["kind"])
        cells[2].text = c["old_text"] or ""
        cells[3].text = c["new_text"] or ""
        cells[4].text = (a.note or "") if a else ""
        cells[5].text = ", ".join(a.tags) if a and a.tags else ""

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def highlighted_pdf(comparison, side: str) -> bytes:
    """One side's rendered pages with overlay boxes burned in, as a PDF."""
    rr = comparison.render_result or {}
    kind_by_change = {c["id"]: c["kind"] for c in rr.get("changes", [])}
    pages = (rr.get(side) or {}).get("pages", [])

    png_pages: List[bytes] = []
    for pg in pages:
        img = _open_page(str(comparison.id), side, pg["n"])
        if img is None:
            continue
        img = img.convert("RGBA")
        w_px, h_px = img.size
        sx = w_px / pg["w_pt"] if pg.get("w_pt") else 1.0
        sy = h_px / pg["h_pt"] if pg.get("h_pt") else 1.0
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for b in pg.get("boxes", []):
            kind = "moved" if kind_by_change.get(b["change_id"]) == "moved" else b["type"]
            draw.rectangle(
                [b["x0"] * sx, b["y0"] * sy, b["x1"] * sx, b["y1"] * sy],
                fill=_FILL.get(kind, _FILL["added"]),
                outline=_OUTLINE.get(kind, _OUTLINE["added"]),
                width=1,
            )
        flat = Image.alpha_composite(img, overlay).convert("RGB")
        out = io.BytesIO()
        flat.save(out, format="PNG")
        png_pages.append(out.getvalue())

    if not png_pages:
        raise ValueError("No rendered pages available to highlight")
    return img2pdf.convert(png_pages)


def side_by_side_pdf(comparison) -> bytes:
    """Old and new pages composited left/right per sheet, as a PDF."""
    rr = comparison.render_result or {}
    old_pages = (rr.get("old") or {}).get("pages", [])
    new_pages = (rr.get("new") or {}).get("pages", [])
    gap = 24
    sheets: List[bytes] = []

    for k in range(max(len(old_pages), len(new_pages))):
        left = _open_page(str(comparison.id), "old", old_pages[k]["n"]) if k < len(old_pages) else None
        right = _open_page(str(comparison.id), "new", new_pages[k]["n"]) if k < len(new_pages) else None
        if left is None and right is None:
            continue
        lw, lh = (left.size if left else (0, 0))
        rw, rh = (right.size if right else (0, 0))
        if left is None:
            lw = rw
        if right is None:
            rw = lw
        canvas = Image.new("RGB", (lw + gap + rw, max(lh, rh) or 1), (255, 255, 255))
        if left is not None:
            canvas.paste(left.convert("RGB"), (0, 0))
        if right is not None:
            canvas.paste(right.convert("RGB"), (lw + gap, 0))
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        sheets.append(out.getvalue())

    if not sheets:
        raise ValueError("No rendered pages available")
    return img2pdf.convert(sheets)


def bundle_zip(comparison, annotations) -> bytes:
    """Report + any available PDFs + the original uploaded inputs, zipped."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("changes-report.docx", changes_report_docx(comparison, annotations))
        if comparison.render_status == "completed" and comparison.render_result:
            try:
                z.writestr("old-highlighted.pdf", highlighted_pdf(comparison, "old"))
                z.writestr("new-highlighted.pdf", highlighted_pdf(comparison, "new"))
                z.writestr("side-by-side.pdf", side_by_side_pdf(comparison))
            except Exception as e:  # noqa: BLE001 — report + inputs still ship
                logger.warning("bundle_zip: skipping PDFs for %s: %s", comparison.id, e)
        for label, path in (("original", comparison.old_file_path), ("revised", comparison.new_file_path)):
            if path and os.path.exists(path):
                z.write(path, arcname=f"inputs/{label}-{os.path.basename(path)}")
    return buf.getvalue()
