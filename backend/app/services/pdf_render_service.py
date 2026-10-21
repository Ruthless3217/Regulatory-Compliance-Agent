"""Normalize any comparable document to PDF, render its pages to PNG, and extract
its words with coordinates (PDF points, top-left origin) for the pixel-faithful
Compare view. No LLM calls."""
import os
import logging
from dataclasses import dataclass
from typing import List, Tuple

from app.services.comparison_service import _detect_running_lines, page_furniture_indices

logger = logging.getLogger(__name__)


@dataclass
class PositionedWord:
    text: str
    page: int          # 1-based
    x0: float
    y0: float          # top
    x1: float
    y1: float          # bottom
    confidence: float = 1.0
    line_id: int = 0
    col_id: int = 0
    block_id: int = 0   # reading-order block (table cell / column / paragraph region)


@dataclass
class PageMeta:
    n: int             # 1-based
    w_pt: float
    h_pt: float
    image_path: str


def to_pdf(file_path: str, content_type: str, out_dir: str, side: str) -> str:
    """Return a path to a PDF representation of the input.

    pdf -> passthrough. docx -> converted via the Gotenberg sidecar
    (``gotenberg_client.convert_to_pdf``'s LibreOffice route) and written into
    ``out_dir`` — this is the DOCX re-enable seam noted in ``okf/log.md``,
    now used by ``submission_export_service`` for the *.pdf export kinds.
    The Compare/pixel-render call sites (``render_orchestrator``,
    ``submission_render_service``) still gate on content_type == "pdf" before
    ever reaching here, so this doesn't change their PDF-only behaviour.
    Anything else raises: no page layout to render. Callers map the raise to
    ``render_status='skipped'`` and fall back to the text redline."""
    if content_type == "pdf":
        return file_path
    if content_type == "docx":
        from app.services.gotenberg_client import convert_to_pdf
        from app.services.docx_revisions import accept_all_revisions
        os.makedirs(out_dir, exist_ok=True)
        # Render the *accepted* document: LibreOffice would otherwise draw tracked
        # deletions (struck text, deleted rows) that the text extractor already ignores.
        accepted = accept_all_revisions(file_path, os.path.join(out_dir, f"{side}.accepted.docx"))
        pdf_bytes = convert_to_pdf(accepted)
        out_path = os.path.join(out_dir, f"{side}.pdf")
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)
        return out_path
    raise ValueError(f"content_type {content_type!r} has no page layout to render (PDF-only)")


def render_pages(pdf_path: str, out_dir: str, cap: int) -> Tuple[List[PageMeta], int]:
    """Render up to `cap` pages of `pdf_path` to PNGs in `out_dir`.

    Returns (metas, truncated_pages). Image files are named page-0001.png…"""
    import pypdfium2 as pdfium

    os.makedirs(out_dir, exist_ok=True)
    metas: List[PageMeta] = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        total = len(pdf)
        n_render = min(total, cap)
        for i in range(n_render):
            page = pdf[i]
            # scale 2.0 ≈ 144 DPI — crisp without being huge.
            bitmap = page.render(scale=2.0)
            pil = bitmap.to_pil()
            image_path = os.path.join(out_dir, f"page-{i + 1:04d}.png")
            pil.save(image_path)
            w_pt, h_pt = page.get_size()   # points (1/72 inch)
            metas.append(PageMeta(n=i + 1, w_pt=float(w_pt), h_pt=float(h_pt), image_path=image_path))
        return metas, max(0, total - n_render)
    finally:
        pdf.close()


_RUPEE_GLYPH = "`"  # Indian-rupee fonts (e.g. "Rupee Foradian") map ₹ onto the backtick


def _font_normalized_text(text: str, fontname: str) -> str:
    """Text of a run as the reader sees it: a backtick drawn in a rupee font is ₹."""
    if _RUPEE_GLYPH in text and "rupee" in (fontname or "").lower():
        return text.replace(_RUPEE_GLYPH, "₹")
    return text


def _word_text(w: dict) -> str:
    chars = w.get("chars")
    if not chars or _RUPEE_GLYPH not in w["text"]:
        return w["text"]
    return "".join(_font_normalized_text(c["text"], c.get("fontname", "")) for c in chars)


def _split_word_by_cells(word: dict, cells) -> List[dict]:
    """A 'word' whose glyphs sit in two ruled-table cells ("DELHIAsaf" - a city cell and an
    address cell drawn touching) is split at the cell border, so each cell keeps its text."""
    chars = word.get("chars") or []
    if not cells or len(chars) < 2:
        return [word]

    def cell_of(c):
        cx, cy = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
        for k, (x0, top, x1, bottom) in enumerate(cells):
            if x0 <= cx <= x1 and top <= cy <= bottom:
                return k
        return None

    groups: List[List[dict]] = []
    last = object()
    for c in chars:
        k = cell_of(c)
        if groups and k == last:
            groups[-1].append(c)
        else:
            groups.append([c])
            last = k
    if len(groups) == 1:
        return [word]
    out = []
    for g in groups:
        text = "".join(_font_normalized_text(c["text"], c.get("fontname", "")) for c in g).strip()
        if text:
            out.append({"text": text, "x0": min(c["x0"] for c in g), "x1": max(c["x1"] for c in g),
                        "top": min(c["top"] for c in g), "bottom": max(c["bottom"] for c in g), "chars": g})
    return out or [word]


def _table_cells(page) -> List[Tuple[float, float, float, float]]:
    """Ruled-table cell boxes (top-left origin) for reading-order repair; [] on failure."""
    try:
        return [tuple(map(float, c)) for t in page.find_tables() for c in t.cells if c]
    except Exception as e:  # noqa: BLE001 — layout hint only, never fatal
        logger.warning("table detection failed on page %s: %s", page.page_number, e)
        return []


def positioned_words(pdf_path: str) -> List[PositionedWord]:
    """Words in reading order with bboxes (PDF points), running headers/footers
    and page-number lines removed (same policy as the text extractor)."""
    return positioned_words_split(pdf_path)[0]


def positioned_words_split(pdf_path: str) -> Tuple[List[PositionedWord], List[PositionedWord]]:
    """(body words in reading order, page furniture words) with bboxes (PDF points).

    Furniture = running headers/footers and page-number lines in the top/bottom band;
    it is kept out of the body alignment and compared page against page instead.

    Evaluates per-page text quality and falls back to OCR (PaddleOCR / Tesseract)
    for scanned/image or corrupted pages.
    """
    import pdfplumber
    from app.config import settings
    from app.services.comparison_ocr_service import is_text_usable, ocr_page_to_words

    from app.services.compare_layout import order_words

    page_lines: List[List[str]] = []
    raw: List[List[dict]] = []
    page_sizes: dict = {}
    table_cells: dict = {}

    with pdfplumber.open(pdf_path) as pdf:
        pdfium_doc = None
        try:
            for page_no, page in enumerate(pdf.pages, start=1):
                page_sizes[page_no] = (float(page.width), float(page.height))
                cells = _table_cells(page)
                words = [
                    part
                    for w in (page.extract_words(return_chars=True) or [])
                    for part in _split_word_by_cells(dict(w, text=_word_text(w)), cells)
                ]
                page_text = " ".join(w["text"] for w in words)
                ocr_used = False

                # Check if page text needs OCR
                if not is_text_usable(page_text) and settings.compare_ocr_enabled and page_no <= settings.compare_ocr_page_cap:
                    if pdfium_doc is None:
                        try:
                            import pypdfium2 as pdfium
                            pdfium_doc = pdfium.PdfDocument(pdf_path)
                        except Exception as e:
                            logger.warning("positioned_words: failed to open pypdfium2 for %s: %s", pdf_path, e)

                    if pdfium_doc is not None and (page_no - 1) < len(pdfium_doc):
                        try:
                            pdfium_page = pdfium_doc[page_no - 1]
                            w_pt, h_pt = pdfium_page.get_size()
                            pil = pdfium_page.render(scale=2.0, draw_annots=False).to_pil()
                            ocr_words = ocr_page_to_words(pil, page_no, float(w_pt), float(h_pt))
                            if ocr_words:
                                words = [
                                    {"text": ow.text, "x0": ow.x0, "top": ow.y0, "x1": ow.x1, "bottom": ow.y1}
                                    for ow in ocr_words
                                ]
                                ocr_used = True
                        except Exception as e:
                            logger.warning("positioned_words OCR failed on page %d: %s", page_no, e)

                if not ocr_used:
                    table_cells[page_no] = cells
                raw.append(words)
                # group words into visual lines (by rounded top) to reuse running-line detection
                lines: dict = {}
                for w in words:
                    lines.setdefault(round(w["top"]), []).append(w["text"])
                page_lines.append([" ".join(lines[k]) for k in sorted(lines)])
        finally:
            if pdfium_doc is not None:
                pdfium_doc.close()

    running = _detect_running_lines(page_lines)

    out: List[PositionedWord] = []
    furniture: List[PositionedWord] = []
    for pi, words in enumerate(raw):
        # rebuild per-line text to drop whole running/page-number lines
        by_top: dict = {}
        for w in words:
            by_top.setdefault(round(w["top"]), []).append(w)
        page_rows = [by_top[k] for k in sorted(by_top)]
        drop = page_furniture_indices([" ".join(w["text"] for w in r) for r in page_rows], running)
        for li, line_words in enumerate(page_rows):
            for w in sorted(line_words, key=lambda w: w["x0"]) if li in drop else line_words:
                (furniture if li in drop else out).append(PositionedWord(
                    text=w["text"], page=pi + 1,
                    x0=float(w["x0"]), y0=float(w["top"]),
                    x1=float(w["x1"]), y1=float(w["bottom"]),
                ))
    # Content reading order: column by column, ruled-table cells row-major, stable
    # visual lines — instead of y-sorting across the full page width.
    return order_words(out, page_sizes=page_sizes, table_cells=table_cells), furniture
