"""OCR abstraction service for document comparison.

Provides page-level OCR adapter supporting PaddleOCR (primary) and Tesseract (fallback),
along with page-level text quality and garbage detection heuristics.
Normalizes all OCR outputs into application-neutral OCRWord and PositionedWord models
with correct PDF-point coordinate conversions.
"""
import os
import re
import logging
from dataclasses import dataclass, field
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
    """Application-neutral word token with PDF-point bounding box and structural identifiers."""
    text: str
    confidence: float
    page: int          # 1-based page index
    x0: float          # left in PDF points (top-left origin)
    y0: float          # top in PDF points
    x1: float          # right in PDF points
    y1: float          # bottom in PDF points
    line_id: int = 0
    block_id: int = 0
    order: int = 0


@dataclass
class OCRLine:
    """A visual line of recognized text with bounding box and child words."""
    text: str
    confidence: float
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    line_id: int = 0
    block_id: int = 0
    words: List[OCRWord] = field(default_factory=list)


@dataclass
class OCRBlock:
    """A structural block/paragraph of lines."""
    text: str
    confidence: float
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    block_id: int = 0
    lines: List[OCRLine] = field(default_factory=list)


@dataclass
class OCRPage:
    """Full OCR representation of a document page preserving layout hierarchy."""
    page: int
    width_pt: float
    height_pt: float
    image_width_px: float
    image_height_px: float
    blocks: List[OCRBlock] = field(default_factory=list)
    lines: List[OCRLine] = field(default_factory=list)
    words: List[OCRWord] = field(default_factory=list)


@dataclass
class OCRDocument:
    """Full OCR document across all processed pages."""
    pages: List[OCRPage] = field(default_factory=list)


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
    threshold = (
        garbage_threshold
        if garbage_threshold is not None
        else getattr(settings, "compare_ocr_garbage_threshold", 0.2)
    )
    if total_non_ws >= 15:
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
            _paddle_ocr_engine = PaddleOCR(
                use_angle_cls=True,
                lang="en",
                show_log=False,
            )
        except Exception as e:
            logger.warning("Failed to initialize PaddleOCR engine: %s", e)
            raise
    return _paddle_ocr_engine


def ocr_bbox_to_page_bbox(
    px_bbox: Tuple[float, float, float, float],
    img_w: float,
    img_h: float,
    pdf_w: float,
    pdf_h: float,
) -> Tuple[float, float, float, float]:
    """Transform raster image pixel coordinates (px_x0, px_y0, px_x1, px_y1)

    into PDF-point coordinates (w_pt, h_pt, top-left origin).
    Clamps coordinates within the target page boundaries.
    """
    if img_w <= 0 or img_h <= 0 or pdf_w <= 0 or pdf_h <= 0:
        return px_bbox
    scale_x = pdf_w / img_w
    scale_y = pdf_h / img_h
    x0 = max(0.0, min(pdf_w, px_bbox[0] * scale_x))
    y0 = max(0.0, min(pdf_h, px_bbox[1] * scale_y))
    x1 = max(0.0, min(pdf_w, px_bbox[2] * scale_x))
    y1 = max(0.0, min(pdf_h, px_bbox[3] * scale_y))
    return (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))


# Alias for backward compatibility
_convert_bbox_to_pdf_points = ocr_bbox_to_page_bbox


def _split_line_into_words(
    line_text: str,
    line_bbox_pt: Tuple[float, float, float, float],
    confidence: float,
    page: int,
    line_id: int = 0,
    block_id: int = 0,
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
                line_id=line_id,
                block_id=block_id,
                order=0,
            )
        ]

    total_len = len(line_text)
    out: List[OCRWord] = []
    current_pos = 0

    for idx, w in enumerate(words):
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
                line_id=line_id,
                block_id=block_id,
                order=idx,
            )
        )

    return out


def _cluster_lines_into_blocks(lines: List[OCRLine]) -> List[OCRBlock]:
    """Cluster OCR lines into paragraph-like blocks based on vertical spacing."""
    if not lines:
        return []
    blocks: List[OCRBlock] = []
    cur_lines: List[OCRLine] = []
    block_idx = 0

    def flush():
        nonlocal cur_lines, block_idx
        if not cur_lines:
            return
        bx0 = min(ln.x0 for ln in cur_lines)
        by0 = min(ln.y0 for ln in cur_lines)
        bx1 = max(ln.x1 for ln in cur_lines)
        by1 = max(ln.y1 for ln in cur_lines)
        btext = " ".join(ln.text for ln in cur_lines)
        bconf = sum(ln.confidence for ln in cur_lines) / len(cur_lines)
        for ln in cur_lines:
            ln.block_id = block_idx
            for w in ln.words:
                w.block_id = block_idx
        blocks.append(
            OCRBlock(
                text=btext,
                confidence=round(bconf, 2),
                page=cur_lines[0].page,
                x0=bx0,
                y0=by0,
                x1=bx1,
                y1=by1,
                block_id=block_idx,
                lines=list(cur_lines),
            )
        )
        block_idx += 1
        cur_lines = []

    for ln in lines:
        if not cur_lines:
            cur_lines.append(ln)
            continue
        prev = cur_lines[-1]
        prev_h = max(10.0, prev.y1 - prev.y0)
        v_gap = ln.y0 - prev.y1
        if v_gap <= 1.8 * prev_h and abs(ln.x0 - prev.x0) <= 80.0:
            cur_lines.append(ln)
        else:
            flush()
            cur_lines.append(ln)
    flush()
    return blocks


def _run_paddle_ocr_page(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> OCRPage:
    """Execute PaddleOCR on a PIL image and return a complete hierarchical OCRPage."""
    import numpy as np
    ocr_engine = get_paddle_ocr_engine()

    img_np = np.array(pil_img)
    img_w, img_h = pil_img.size

    result = ocr_engine.ocr(img_np, cls=True)
    if not result or not result[0]:
        return OCRPage(page=page, width_pt=pdf_w, height_pt=pdf_h, image_width_px=img_w, image_height_px=img_h)

    min_conf = getattr(settings, "compare_ocr_min_confidence", 0.5)
    ocr_lines: List[OCRLine] = []
    all_words: List[OCRWord] = []

    raw_lines = result[0]
    sorted_lines = sorted(
        raw_lines,
        key=lambda item: (min(pt[1] for pt in item[0]), min(pt[0] for pt in item[0]))
    )

    for line_idx, line_info in enumerate(sorted_lines):
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

        bbox_pt = ocr_bbox_to_page_bbox(
            (px_x0, px_y0, px_x1, px_y1),
            img_w, img_h, pdf_w, pdf_h
        )

        words = _split_line_into_words(text, bbox_pt, conf, page, line_id=line_idx)
        line_obj = OCRLine(
            text=text,
            confidence=round(conf, 2),
            page=page,
            x0=bbox_pt[0],
            y0=bbox_pt[1],
            x1=bbox_pt[2],
            y1=bbox_pt[3],
            line_id=line_idx,
            words=words,
        )
        ocr_lines.append(line_obj)
        all_words.extend(words)

    blocks = _cluster_lines_into_blocks(ocr_lines)
    return OCRPage(
        page=page,
        width_pt=pdf_w,
        height_pt=pdf_h,
        image_width_px=img_w,
        image_height_px=img_h,
        blocks=blocks,
        lines=ocr_lines,
        words=all_words,
    )


def _run_paddle_ocr(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Execute PaddleOCR on a PIL image and return normalized OCRWord instances."""
    ocr_page = _run_paddle_ocr_page(pil_img, page, pdf_w, pdf_h)
    if ocr_page.words:
        avg_conf = sum(w.confidence for w in ocr_page.words) / len(ocr_page.words)
        logger.info(
            "Compare OCR: page=%d source=paddleocr words=%d blocks=%d avg_confidence=%.2f",
            page, len(ocr_page.words), len(ocr_page.blocks), avg_conf
        )
    return ocr_page.words


def _run_tesseract_ocr_page(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> OCRPage:
    """Execute Tesseract OCR and construct a complete OCRPage hierarchy."""
    import pytesseract
    img_w, img_h = pil_img.size
    min_conf = getattr(settings, "compare_ocr_min_confidence", 0.5)

    try:
        data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
    except Exception as e:
        logger.warning("Tesseract image_to_data failed on page %d: %s; trying image_to_string", page, e)
        text = pytesseract.image_to_string(pil_img) or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        ocr_lines: List[OCRLine] = []
        all_words: List[OCRWord] = []
        for li, ln in enumerate(lines):
            y0_pt = (li / max(1, len(lines))) * pdf_h
            y1_pt = ((li + 1) / max(1, len(lines))) * pdf_h
            bbox_pt = (20.0, y0_pt, pdf_w - 20.0, y1_pt)
            words = _split_line_into_words(ln, bbox_pt, 0.7, page, line_id=li)
            ocr_lines.append(OCRLine(text=ln, confidence=0.7, page=page, x0=20.0, y0=y0_pt, x1=pdf_w - 20.0, y1=y1_pt, line_id=li, words=words))
            all_words.extend(words)
        blocks = _cluster_lines_into_blocks(ocr_lines)
        return OCRPage(page=page, width_pt=pdf_w, height_pt=pdf_h, image_width_px=img_w, image_height_px=img_h, blocks=blocks, lines=ocr_lines, words=all_words)

    ocr_words: List[OCRWord] = []
    lines_by_id: Dict[int, List[OCRWord]] = {}
    n_boxes = len(data.get("text", []))

    for i in range(n_boxes):
        word_text = (data["text"][i] or "").strip()
        conf_raw = data.get("conf", [0])[i]
        line_num = data.get("line_num", [0])[i]
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

        x0_pt, y0_pt, x1_pt, y1_pt = ocr_bbox_to_page_bbox(
            (px_x0, px_y0, px_x1, px_y1),
            img_w, img_h, pdf_w, pdf_h
        )

        w_obj = OCRWord(
            text=word_text,
            confidence=round(conf, 2),
            page=page,
            x0=round(x0_pt, 2),
            y0=round(y0_pt, 2),
            x1=round(x1_pt, 2),
            y1=round(y1_pt, 2),
            line_id=line_num,
        )
        ocr_words.append(w_obj)
        lines_by_id.setdefault(line_num, []).append(w_obj)

    ocr_lines: List[OCRLine] = []
    for l_id, l_words in sorted(lines_by_id.items()):
        l_text = " ".join(w.text for w in l_words)
        l_conf = sum(w.confidence for w in l_words) / len(l_words)
        ocr_lines.append(OCRLine(
            text=l_text,
            confidence=round(l_conf, 2),
            page=page,
            x0=min(w.x0 for w in l_words),
            y0=min(w.y0 for w in l_words),
            x1=max(w.x1 for w in l_words),
            y1=max(w.y1 for w in l_words),
            line_id=l_id,
            words=l_words,
        ))

    blocks = _cluster_lines_into_blocks(ocr_lines)
    return OCRPage(
        page=page,
        width_pt=pdf_w,
        height_pt=pdf_h,
        image_width_px=img_w,
        image_height_px=img_h,
        blocks=blocks,
        lines=ocr_lines,
        words=ocr_words,
    )


def _run_tesseract_ocr(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Execute Tesseract OCR via pytesseract as fallback, extracting word-level bounding boxes."""
    ocr_page = _run_tesseract_ocr_page(pil_img, page, pdf_w, pdf_h)
    if ocr_page.words:
        logger.info("Compare OCR: page=%d source=tesseract words=%d", page, len(ocr_page.words))
    return ocr_page.words


def _words_to_ocr_page(
    words: List[OCRWord],
    page: int,
    pdf_w: float,
    pdf_h: float,
    img_w: float,
    img_h: float,
) -> OCRPage:
    """Construct a hierarchical OCRPage from a flat list of OCRWords."""
    if not words:
        return OCRPage(page=page, width_pt=pdf_w, height_pt=pdf_h, image_width_px=img_w, image_height_px=img_h)

    lines_by_id: Dict[int, List[OCRWord]] = {}
    for w in words:
        lid = getattr(w, "line_id", 0)
        lines_by_id.setdefault(lid, []).append(w)

    ocr_lines: List[OCRLine] = []
    for lid, lwords in sorted(lines_by_id.items()):
        lw = sorted(lwords, key=lambda x: x.x0)
        lx0 = min(w.x0 for w in lw)
        ly0 = min(w.y0 for w in lw)
        lx1 = max(w.x1 for w in lw)
        ly1 = max(w.y1 for w in lw)
        ltext = " ".join(w.text for w in lw)
        lconf = sum(w.confidence for w in lw) / len(lw)
        ocr_lines.append(OCRLine(
            text=ltext,
            confidence=round(lconf, 2),
            page=page,
            x0=lx0,
            y0=ly0,
            x1=lx1,
            y1=ly1,
            line_id=lid,
            words=lw,
        ))

    blocks = _cluster_lines_into_blocks(ocr_lines)
    return OCRPage(
        page=page,
        width_pt=pdf_w,
        height_pt=pdf_h,
        image_width_px=img_w,
        image_height_px=img_h,
        blocks=blocks,
        lines=ocr_lines,
        words=words,
    )


class OCRProvider:
    """Base abstract interface for page-level OCR engines."""
    def extract_page(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> OCRPage:
        raise NotImplementedError

    def extract_words(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> List[OCRWord]:
        raise NotImplementedError


class PaddleOCRProvider(OCRProvider):
    """Primary OCR provider using PaddleOCR with angle classification."""
    def extract_page(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> OCRPage:
        try:
            res = _run_paddle_ocr(pil_img, page, pdf_w, pdf_h)
            if isinstance(res, OCRPage):
                return res
            if isinstance(res, list):
                return _words_to_ocr_page(res, page, pdf_w, pdf_h, float(pil_img.size[0]), float(pil_img.size[1]))
        except Exception:
            raise
        return _run_paddle_ocr_page(pil_img, page, pdf_w, pdf_h)

    def extract_words(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> List[OCRWord]:
        res = _run_paddle_ocr(pil_img, page, pdf_w, pdf_h)
        if isinstance(res, OCRPage):
            return res.words
        return res


class TesseractOCRProvider(OCRProvider):
    """Fallback OCR provider using pytesseract."""
    def extract_page(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> OCRPage:
        try:
            res = _run_tesseract_ocr(pil_img, page, pdf_w, pdf_h)
            if isinstance(res, OCRPage):
                return res
            if isinstance(res, list):
                return _words_to_ocr_page(res, page, pdf_w, pdf_h, float(pil_img.size[0]), float(pil_img.size[1]))
        except Exception:
            raise
        return _run_tesseract_ocr_page(pil_img, page, pdf_w, pdf_h)

    def extract_words(
        self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float
    ) -> List[OCRWord]:
        res = _run_tesseract_ocr(pil_img, page, pdf_w, pdf_h)
        if isinstance(res, OCRPage):
            return res.words
        return res


class OCRResultCache:
    """In-memory cache for page OCR extraction results."""
    def __init__(self, max_size: int = 500):
        self._cache: Dict[str, OCRPage] = {}
        self._max_size = max_size

    def _compute_key(self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float, engine: str = "") -> str:
        import hashlib
        img_bytes = pil_img.tobytes()
        h = hashlib.sha256(img_bytes).hexdigest()[:16]
        return f"p{page}_{int(pdf_w)}x{int(pdf_h)}_{engine}_{h}"

    def get_page(self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float, engine: str = "") -> Optional[OCRPage]:
        if not getattr(settings, "compare_ocr_cache_enabled", True):
            return None
        key = self._compute_key(pil_img, page, pdf_w, pdf_h, engine)
        return self._cache.get(key)

    def get(self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float, engine: str = "") -> Optional[List[OCRWord]]:
        page_obj = self.get_page(pil_img, page, pdf_w, pdf_h, engine)
        return page_obj.words if page_obj else None

    def set_page(self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float, ocr_page: OCRPage, engine: str = "") -> None:
        if not getattr(settings, "compare_ocr_cache_enabled", True):
            return
        if len(self._cache) >= self._max_size:
            self._cache.clear()
        key = self._compute_key(pil_img, page, pdf_w, pdf_h, engine)
        self._cache[key] = ocr_page

    def set(self, pil_img: Image.Image, page: int, pdf_w: float, pdf_h: float, words: List[OCRWord], engine: str = "") -> None:
        ocr_page = OCRPage(
            page=page,
            width_pt=pdf_w,
            height_pt=pdf_h,
            image_width_px=float(pil_img.size[0]),
            image_height_px=float(pil_img.size[1]),
            words=words,
        )
        self.set_page(pil_img, page, pdf_w, pdf_h, ocr_page, engine)

    def clear(self) -> None:
        self._cache.clear()


ocr_cache = OCRResultCache()
paddle_provider = PaddleOCRProvider()
tesseract_provider = TesseractOCRProvider()


def ocr_page_to_page(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> Optional[OCRPage]:
    """Extract complete hierarchical OCRPage with PDF-point coordinates and caching."""
    if not getattr(settings, "compare_ocr_enabled", True):
        return None

    engine = getattr(settings, "compare_ocr_engine", "paddle").lower()
    cached = ocr_cache.get_page(pil_img, page, pdf_w, pdf_h, engine)
    if cached is not None:
        return cached

    ocr_page: Optional[OCRPage] = None
    if engine == "paddle":
        try:
            ocr_page = paddle_provider.extract_page(pil_img, page, pdf_w, pdf_h)
        except Exception as e:
            logger.warning("Compare OCR: page=%d source=tesseract reason=paddleocr_failure (%s)", page, e)
            try:
                ocr_page = tesseract_provider.extract_page(pil_img, page, pdf_w, pdf_h)
            except Exception as e2:
                logger.error("Compare OCR fallback tesseract also failed on page %d: %s", page, e2)
                ocr_page = None
    else:
        try:
            ocr_page = tesseract_provider.extract_page(pil_img, page, pdf_w, pdf_h)
        except Exception as e:
            logger.warning("Compare OCR: page=%d tesseract failed (%s); trying paddleocr", page, e)
            try:
                ocr_page = paddle_provider.extract_page(pil_img, page, pdf_w, pdf_h)
            except Exception as e2:
                logger.error("Compare OCR paddleocr also failed on page %d: %s", page, e2)
                ocr_page = None

    if ocr_page:
        ocr_cache.set_page(pil_img, page, pdf_w, pdf_h, ocr_page, engine)
    return ocr_page


def ocr_page_to_words(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[OCRWord]:
    """Extract OCR words with PDF-point bounding boxes for a single page with caching."""
    ocr_page = ocr_page_to_page(pil_img, page, pdf_w, pdf_h)
    return ocr_page.words if ocr_page else []


def ocr_page_to_lines(
    pil_img: Image.Image,
    page: int,
    pdf_w: float,
    pdf_h: float,
) -> List[str]:
    """OCR a page and group recognized words into visual text lines."""
    ocr_page = ocr_page_to_page(pil_img, page, pdf_w, pdf_h)
    if not ocr_page or not ocr_page.lines:
        words = ocr_page_to_words(pil_img, page, pdf_w, pdf_h)
        if not words:
            return []
        lines_by_y: Dict[int, List[OCRWord]] = {}
        for w in words:
            line_key = round(w.y0 / 6.0) * 6
            lines_by_y.setdefault(line_key, []).append(w)
        out_lines: List[str] = []
        for k in sorted(lines_by_y.keys()):
            line_words = sorted(lines_by_y[k], key=lambda item: item.x0)
            line_str = " ".join(w.text for w in line_words).strip()
            if line_str:
                out_lines.append(line_str)
        return out_lines

    return [ln.text.strip() for ln in ocr_page.lines if ln.text.strip()]
