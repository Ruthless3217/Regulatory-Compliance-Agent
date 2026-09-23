"""OCR abstraction service for document comparison.

Provides page-level OCR adapter supporting PaddleOCR (primary) and Tesseract (fallback),
along with page-level text quality and garbage detection heuristics.
Normalizes all OCR outputs into application-neutral OCRWord and PositionedWord models
with correct PDF-point coordinate conversions.
"""
import os
import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

# Garbage detection patterns
_CID_PATTERN = re.compile(r"\(cid:\d+\)")
_REPLACEMENT_CHAR = "\ufffd"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class OCRWord:
    """Application-neutral word token with PDF-point bounding box."""
    text: str
    confidence: float
    page: int          # 1-based page index
    x0: float          # left in PDF points (top-left origin)
    y0: float          # top in PDF points
    x1: float          # right in PDF points
    y1: float          # bottom in PDF points


def is_text_usable(text: str, garbage_threshold: Optional[float] = None) -> bool:
    """Evaluate whether extracted text represents a valid, readable text layer.

    Returns False for:
    - Empty or whitespace-only text
    - Text dominated by font encoding errors (e.g. (cid:123) sequences)
    - Text containing excessive Unicode replacement characters (\\ufffd)
    - Text with an abnormally low alphanumeric character ratio (corrupted glyphs/symbols)

    Conservative: Standard insurance/compliance text containing policy codes (e.g. 116N216V01),
    dates, currency symbols, percentages, tables, disclaimers, or UINs will return True.
    """
    if not text or not text.strip():
        return False

    raw = text.strip()
    non_ws = re.sub(r"\s+", "", raw)
    total_non_ws = len(non_ws)
    if total_non_ws == 0:
        return False

    # 1. CID font encoding failures: e.g. (cid:10) (cid:25)
    cid_matches = _CID_PATTERN.findall(raw)
    if cid_matches:
        cid_char_count = sum(len(m) for m in cid_matches)
        if len(cid_matches) >= 2 and (cid_char_count / total_non_ws) > 0.25:
            logger.info("Page text deemed unusable: high CID ratio (%d matches, %.1f%%)",
                        len(cid_matches), (cid_char_count / total_non_ws) * 100)
            return False

    # 2. Unicode replacement character check
    replacement_count = raw.count(_REPLACEMENT_CHAR)
    if replacement_count >= 2 and (replacement_count / total_non_ws) > 0.10:
        logger.info("Page text deemed unusable: high replacement char ratio (%d / %d)",
                    replacement_count, total_non_ws)
        return False

    # 3. Alphanumeric ratio check (for strings of meaningful length)
    # Only applies when text has at least 15 non-whitespace chars
    threshold = (
        garbage_threshold
        if garbage_threshold is not None
        else getattr(settings, "compare_ocr_garbage_threshold", 0.2)
    )
    if total_non_ws >= 15:
        # Count alphanumeric characters (ASCII + unicode letters/digits)
        alnum_count = sum(1 for c in non_ws if c.isalnum())
        ratio = alnum_count / total_non_ws
        if ratio < threshold:
            logger.info("Page text deemed unusable: low alphanumeric ratio (%.2f < %.2f, text sample: %r)",
                        ratio, threshold, raw[:60])
            return False

    return True


def is_page_text_usable(page_lines: List[str]) -> bool:
    """Check whether a page's collection of text lines is usable."""
    if not page_lines:
        return False
    joined = " ".join(ln for ln in page_lines if ln and ln.strip())
    return is_text_usable(joined)


# Global cached PaddleOCR engine instance
_paddle_ocr_engine = None


def get_paddle_ocr_engine():
    """Lazily instantiate and cache the PaddleOCR engine."""
    global _paddle_ocr_engine
    if _paddle_ocr_engine is None:
        try:
            from paddleocr import PaddleOCR
            # Initialize with CPU-friendly parameters and english language
            _paddle_ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang="en",
                show_log=False,
            )
        except Exception as e:
            logger.warning("Failed to initialize PaddleOCR engine: %s", e)
            raise
    return _paddle_ocr_engine


def _convert_bbox_to_pdf_points(
    px_bbox: Tuple[float, float, float, float],
    img_w: float,
    img_h: float,
    pdf_w: float,
    pdf_h: float,
) -> Tuple[float, float, float, float]:
    """Convert pixel bounding box (x0, y0, x1, y1) to PDF points (w_pt, h_pt)."""
    if img_w <= 0 or img_h <= 0 or pdf_w <= 0 or pdf_h <= 0:
        return px_bbox
    scale_x = pdf_w / img_w
    scale_y = pdf_h / img_h
    return (
        px_bbox[0] * scale_x,
        px_bbox[1] * scale_y,
        px_bbox[2] * scale_x,
        px_bbox[3] * scale_y,
    )


def _split_line_into_words(
    line_text: str,
    line_bbox_pt: Tuple[float, float, float, float],
    confidence: float,
    page: int,
) -> List[OCRWord]:
    """Split a recognized text line into individual OCRWord tokens with proportional bboxes."""
    words = line_text.split()
    if not words:
        return []

    x0_pt, y0_pt, x1_pt, y1_pt = line_bbox_pt
    line_w_pt = max(0.0, x1_pt - x0_pt)

    if len(words) == 1 or line_w_pt <= 0:
        return [
            OCRWord(
                text=words[0],
                confidence=confidence,
                page=page,
                x0=round(x0_pt, 2),
                y0=round(y0_pt, 2),
                x1=round(x1_pt, 2),
                y1=round(y1_pt, 2),
            )
        ]

    # Calculate proportional horizontal coordinates based on character positions
    total_len = len(line_text)
    out: List[OCRWord] = []
    current_pos = 0

    for w in words:
        start_idx = line_text.find(w, current_pos)
        if start_idx == -1:
            start_idx = current_pos
        end_idx = start_idx + len(w)
        current_pos = end_idx

        w_x0 = x0_pt + (start_idx / total_len) * line_w_pt
        w_x1 = x0_pt + (end_idx / total_len) * line_w_pt

        out.append(
            OCRWord(
                text=w,
                confidence=confidence,
                page=page,
                x0=round(w_x0, 2),
                y0=round(y0_pt, 2),
                x1=round(w_x1, 2),
                y1=round(y1_pt, 2),
            )
        )

    return out


def _run_paddle_ocr(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Execute PaddleOCR on a PIL image and return normalized OCRWord instances."""
    import numpy as np
    ocr_engine = get_paddle_ocr_engine()
    
    img_np = np.array(pil_img)
    img_w, img_h = pil_img.size

    # PaddleOCR returns: [ [ [ [x0,y0],[x1,y1],[x2,y2],[x3,y3] ], (text, score) ], ... ]
    result = ocr_engine.ocr(img_np, cls=True)
    if not result or not result[0]:
        return []

    min_conf = getattr(settings, "compare_ocr_min_confidence", 0.5)
    ocr_words: List[OCRWord] = []

    # Sort boxes in top-to-bottom reading order with left-to-right secondary sort
    lines = result[0]
    # Each item: [points, (text, confidence)]
    sorted_lines = sorted(
        lines,
        key=lambda item: (min(pt[1] for pt in item[0]), min(pt[0] for pt in item[0]))
    )

    for line_info in sorted_lines:
        if not line_info or len(line_info) < 2:
            continue
        poly, text_conf = line_info[0], line_info[1]
        text, conf = text_conf[0], float(text_conf[1])
        if conf < min_conf or not text.strip():
            continue

        px_x0 = min(pt[0] for pt in poly)
        px_y0 = min(pt[1] for pt in poly)
        px_x1 = max(pt[0] for pt in poly)
        px_y1 = max(pt[1] for pt in poly)

        bbox_pt = _convert_bbox_to_pdf_points(
            (px_x0, px_y0, px_x1, px_y1),
            img_w, img_h, pdf_w, pdf_h
        )

        words = _split_line_into_words(text, bbox_pt, conf, page)
        ocr_words.extend(words)

    if ocr_words:
        avg_conf = sum(w.confidence for w in ocr_words) / len(ocr_words)
        logger.info(
            "Compare OCR: page=%d source=paddleocr words=%d avg_confidence=%.2f",
            page, len(ocr_words), avg_conf
        )
    return ocr_words


def _run_tesseract_ocr(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Execute Tesseract OCR via pytesseract as fallback, extracting word-level bounding boxes."""
    import pytesseract
    img_w, img_h = pil_img.size
    min_conf = getattr(settings, "compare_ocr_min_confidence", 0.5)

    try:
        data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
    except Exception as e:
        logger.warning("Tesseract image_to_data failed on page %d: %s; trying image_to_string", page, e)
        text = pytesseract.image_to_string(pil_img) or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        out: List[OCRWord] = []
        for li, ln in enumerate(lines):
            # Estimate vertical position across the page
            y0_pt = (li / max(1, len(lines))) * pdf_h
            y1_pt = ((li + 1) / max(1, len(lines))) * pdf_h
            bbox_pt = (20.0, y0_pt, pdf_w - 20.0, y1_pt)
            out.extend(_split_line_into_words(ln, bbox_pt, 0.7, page))
        return out

    ocr_words: List[OCRWord] = []
    n_boxes = len(data.get("text", []))

    for i in range(n_boxes):
        word_text = (data["text"][i] or "").strip()
        conf_raw = data.get("conf", [0])[i]
        try:
            conf = float(conf_raw) / 100.0 if float(conf_raw) > 0 else 0.0
        except (ValueError, TypeError):
            conf = 0.0

        if not word_text or (conf > 0 and conf < min_conf):
            continue

        px_x0 = float(data["left"][i])
        px_y0 = float(data["top"][i])
        px_x1 = px_x0 + float(data["width"][i])
        px_y1 = px_y0 + float(data["height"][i])

        x0_pt, y0_pt, x1_pt, y1_pt = _convert_bbox_to_pdf_points(
            (px_x0, px_y0, px_x1, px_y1),
            img_w, img_h, pdf_w, pdf_h
        )

        ocr_words.append(
            OCRWord(
                text=word_text,
                confidence=round(conf, 2),
                page=page,
                x0=round(x0_pt, 2),
                y0=round(y0_pt, 2),
                x1=round(x1_pt, 2),
                y1=round(y1_pt, 2),
            )
        )

    if ocr_words:
        logger.info(
            "Compare OCR: page=%d source=tesseract words=%d",
            page, len(ocr_words)
        )
    return ocr_words


def ocr_page_to_words(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Extract OCR words with PDF-point bounding boxes for a single page.

    Tries the configured primary OCR engine (default: PaddleOCR), falling back to
    Tesseract if the primary engine fails or is unavailable.
    """
    if not getattr(settings, "compare_ocr_enabled", True):
        return []

    engine = getattr(settings, "compare_ocr_engine", "paddle").lower()

    if engine == "paddle":
        try:
            return _run_paddle_ocr(pil_img, page, pdf_w, pdf_h)
        except Exception as e:
            logger.warning(
                "Compare OCR: page=%d source=tesseract reason=paddleocr_failure (%s)",
                page, e
            )
            try:
                return _run_tesseract_ocr(pil_img, page, pdf_w, pdf_h)
            except Exception as e2:
                logger.error("Compare OCR fallback tesseract also failed on page %d: %s", page, e2)
                return []
    else:
        # Primary engine is tesseract
        try:
            return _run_tesseract_ocr(pil_img, page, pdf_w, pdf_h)
        except Exception as e:
            logger.warning("Compare OCR: page=%d tesseract failed (%s); trying paddleocr", page, e)
            try:
                return _run_paddle_ocr(pil_img, page, pdf_w, pdf_h)
            except Exception as e2:
                logger.error("Compare OCR paddleocr also failed on page %d: %s", page, e2)
                return []


def ocr_page_to_lines(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[str]:
    """OCR a page and group recognized words into visual text lines."""
    words = ocr_page_to_words(pil_img, page, pdf_w, pdf_h)
    if not words:
        return []

    # Group words into visual lines by vertical position (rounded y0)
    lines_by_y: Dict[int, List[OCRWord]] = {}
    for w in words:
        # Group tolerance: round y0 to nearest 6-8 points
        line_key = round(w.y0 / 6.0) * 6
        lines_by_y.setdefault(line_key, []).append(w)

    sorted_line_keys = sorted(lines_by_y.keys())
    out_lines: List[str] = []
    for k in sorted_line_keys:
        line_words = sorted(lines_by_y[k], key=lambda item: item.x0)
        line_str = " ".join(w.text for w in line_words).strip()
        if line_str:
            out_lines.append(line_str)

    return out_lines
