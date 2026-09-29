"""Normalize any comparable document to PDF, render its pages to PNG, and extract
its words with coordinates (PDF points, top-left origin) for the pixel-faithful
Compare view. No LLM calls."""
import os
import logging
from dataclasses import dataclass
from typing import List, Tuple

from app.services.comparison_service import _detect_running_lines, _PAGE_NUMBER

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
        os.makedirs(out_dir, exist_ok=True)
        pdf_bytes = convert_to_pdf(file_path)
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


def positioned_words(pdf_path: str) -> List[PositionedWord]:
    """Words in reading order with bboxes (PDF points), running headers/footers
    and page-number lines removed (same policy as the text extractor).

    Evaluates per-page text quality and falls back to OCR (PaddleOCR / Tesseract)
    for scanned/image or corrupted pages.
    """
    import pdfplumber
    from app.config import settings
    from app.services.comparison_ocr_service import is_text_usable, ocr_page_to_words

    page_lines: List[List[str]] = []
    raw: List[List[dict]] = []

    with pdfplumber.open(pdf_path) as pdf:
        pdfium_doc = None
        try:
            for page_no, page in enumerate(pdf.pages, start=1):
                words = page.extract_words() or []
                page_text = " ".join(w["text"] for w in words)

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
                        except Exception as e:
                            logger.warning("positioned_words OCR failed on page %d: %s", page_no, e)

                raw.append(words)
                # group words into visual lines (by rounded top) to reuse running-line detection
                lines: dict = {}
                for w in words:
                    lines.setdefault(round(w["top"]), []).append(w["text"])
                page_lines.append([" ".join(v) for v in lines.values()])
        finally:
            if pdfium_doc is not None:
                pdfium_doc.close()

    running = _detect_running_lines(page_lines)

    out: List[PositionedWord] = []
    for pi, words in enumerate(raw):
        # rebuild per-line text to drop whole running/page-number lines
        by_top: dict = {}
        for w in words:
            by_top.setdefault(round(w["top"]), []).append(w)
        for top_key, line_words in by_top.items():
            line_text = " ".join(w["text"] for w in line_words)
            if line_text in running or _PAGE_NUMBER.match(line_text):
                continue
            for w in line_words:
                out.append(PositionedWord(
                    text=w["text"], page=pi + 1,
                    x0=float(w["x0"]), y0=float(w["top"]),
                    x1=float(w["x1"]), y1=float(w["bottom"]),
                ))
    return out
