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


def _load_font(size: int):
    """A TrueType font at ``size`` if one is on the box, else PIL's bitmap default."""
    from PIL import ImageFont
    for path in (
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 — fall through to the next candidate
            continue
    return ImageFont.load_default()


def _numbered_annotations(comparison, annotations):
    """Number the changes that carry a reviewer note/tags, in report order.

    Returns ``(number_by_change_id, rows)`` where each row is
    ``(number, kind, note, tags, old_text, new_text)``. Only annotated changes
    are numbered, so both the on-page badge and the appended comments page use
    the same 1..N sequence.
    """
    ann_by = {a.change_id: a for a in (annotations or [])}
    number_by: dict = {}
    rows: List[dict] = []
    n = 0
    for c in derive_changes(comparison):
        a = ann_by.get(c["change_id"])
        note = (getattr(a, "note", "") or "") if a else ""
        tags = list(getattr(a, "tags", []) or []) if a else []
        if note or tags:
            n += 1
            number_by[c["change_id"]] = n
            rows.append({
                "n": n, "kind": c["kind"], "note": note, "tags": tags,
                "old_text": c.get("old_text", ""), "new_text": c.get("new_text", ""),
            })
    return number_by, rows


def _draw_badge(draw, cx: float, cy: float, num: int, kind: str) -> None:
    """A small filled number badge, centred at (cx, cy), coloured by change kind."""
    r = 11
    color = _OUTLINE.get(kind, _OUTLINE["added"])
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(255, 255, 255, 255))
    font = _load_font(15)
    label = str(num)
    tw = draw.textlength(label, font=font)
    draw.text((cx - tw / 2, cy - 9), label, fill=(255, 255, 255, 255), font=font)


def _comments_pages(rows: List[dict], page_px=(1240, 1754)) -> List[bytes]:
    """Render the reviewer comments as one or more printable PNG pages."""
    W, H = page_px
    margin = 70
    title_font = _load_font(34)
    head_font = _load_font(21)
    body_font = _load_font(20)

    pages: List[Image.Image] = []

    def new_canvas():
        img = Image.new("RGB", (W, H), (255, 255, 255))
        return img, ImageDraw.Draw(img)

    img, draw = new_canvas()

    def wrap(text: str, font, max_w: int) -> List[str]:
        lines: List[str] = []
        cur = ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines or [""]

    y = margin
    draw.text((margin, y), "Reviewer comments", fill=(17, 17, 17), font=title_font)
    y += 56

    text_w = W - 2 * margin - 24
    for row in rows:
        block: List[tuple] = [("head", f"{row['n']}. {_KIND_LABEL.get(row['kind'], row['kind'])}")]
        if row["note"]:
            block += [("body", ln) for ln in wrap(row["note"], body_font, text_w)]
        if row["tags"]:
            block += [("body", ln) for ln in wrap("Tags: " + ", ".join(row["tags"]), body_font, text_w)]

        if y + len(block) * 28 + 16 > H - margin:  # spill to a fresh page
            pages.append(img)
            img, draw = new_canvas()
            y = margin

        for kind, line in block:
            font = head_font if kind == "head" else body_font
            x = margin if kind == "head" else margin + 24
            fill = (17, 17, 17) if kind == "head" else (55, 55, 55)
            draw.text((x, y), line, fill=fill, font=font)
            y += 28
        y += 14

    pages.append(img)
    out: List[bytes] = []
    for p in pages:
        buf = io.BytesIO()
        p.save(buf, format="PNG", dpi=(150, 150))
        out.append(buf.getvalue())
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


def highlighted_pdf(comparison, side: str, annotations=None) -> bytes:
    """One side's rendered pages with overlay boxes burned in, as a PDF.

    Reviewer comments (if any) get a numbered badge on their change's box and a
    "Reviewer comments" page appended at the end, so the exported PDF carries the
    notes — not just the highlights.
    """
    rr = comparison.render_result or {}
    kind_by_change = {c["id"]: c["kind"] for c in rr.get("changes", [])}
    pages = (rr.get(side) or {}).get("pages", [])
    number_by, rows = _numbered_annotations(comparison, annotations)

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
            num = number_by.get(b["change_id"])
            if num is not None:  # this change carries a reviewer comment
                _draw_badge(draw, max(11.0, b["x0"] * sx), max(11.0, b["y0"] * sy), num, kind)
        flat = Image.alpha_composite(img, overlay).convert("RGB")
        out = io.BytesIO()
        flat.save(out, format="PNG")
        png_pages.append(out.getvalue())

    if not png_pages:
        raise ValueError("No rendered pages available to highlight")
    if rows:
        png_pages.extend(_comments_pages(rows))
    return img2pdf.convert(png_pages)


def side_by_side_pdf(comparison, annotations=None) -> bytes:
    """Old and new pages composited left/right per sheet, as a PDF.

    A "Reviewer comments" page is appended when annotations are present.
    """
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
    _, rows = _numbered_annotations(comparison, annotations)
    if rows:
        sheets.extend(_comments_pages(rows))
    return img2pdf.convert(sheets)


def bundle_zip(comparison, annotations) -> bytes:
    """Report + any available PDFs + the original uploaded inputs, zipped."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("changes-report.docx", changes_report_docx(comparison, annotations))
        if comparison.render_status == "completed" and comparison.render_result:
            try:
                z.writestr("old-highlighted.pdf", highlighted_pdf(comparison, "old", annotations))
                z.writestr("new-highlighted.pdf", highlighted_pdf(comparison, "new", annotations))
                z.writestr("side-by-side.pdf", side_by_side_pdf(comparison, annotations))
            except Exception as e:  # noqa: BLE001 — report + inputs still ship
                logger.warning("bundle_zip: skipping PDFs for %s: %s", comparison.id, e)
        for label, path in (("original", comparison.old_file_path), ("revised", comparison.new_file_path)):
            if path and os.path.exists(path):
                z.write(path, arcname=f"inputs/{label}-{os.path.basename(path)}")
    return buf.getvalue()
