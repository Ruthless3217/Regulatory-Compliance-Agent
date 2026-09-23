"""Comprehensive tests for PaddleOCR & Tesseract OCR fallback in Document Compare.

Covers:
1. Normal text PDF (no OCR, native extraction).
2. Fully scanned/image PDF (PaddleOCR extraction & diff).
3. Mixed PDF (page 1 native, page 2 scanned OCR, page 3 native).
4. Garbage/corrupted text layer (CID fonts, replacement chars) triggering OCR.
5. OCR bounding box coordinate transformations (raster pixels -> PDF points).
6. Pixel comparison & RenderResult generation with OCR-derived words.
7. Positioned search across OCR-derived words.
8. Fallback to Tesseract when PaddleOCR is unavailable or raises.
9. OCR disabled behavior (COMPARE_OCR_ENABLED=False).
10. Conservative page quality & garbage detection heuristics.
"""
import io
import sys
import types
from unittest.mock import MagicMock, patch
import img2pdf
import pytest
from PIL import Image, ImageDraw
import pypdf

from app.config import settings
from app.services.comparison_service import (
    extract_pdf_segments,
    _pdf_text_layer_lines,
    build_diff,
)
from app.services.comparison_ocr_service import (
    OCRWord,
    is_text_usable,
    is_page_text_usable,
    ocr_page_to_words,
    ocr_page_to_lines,
    _convert_bbox_to_pdf_points,
    _split_line_into_words,
)
from app.services.pdf_render_service import positioned_words, PositionedWord
from app.services.render_orchestrator import _render_pair
from app.api.routes.comparisons import _search_pdf


def _create_image_pdf(text: str, width: int = 800, height: int = 300) -> bytes:
    """Create a single-page image-only PDF with rendered text."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 80), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    return img2pdf.convert(buf.getvalue())


def _create_native_pdf_bytes(text: str) -> bytes:
    """Create a valid single-page PDF with native text using standard PDF 1.4 syntax."""
    safe_text = text.replace("(", "\\(").replace(")", "\\)")
    content = f"BT\n/F1 12 Tf\n72 700 Td\n({safe_text}) Tj\nET\n".encode("latin1")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []

    offsets.append(len(pdf))
    pdf.extend(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    offsets.append(len(pdf))
    pdf.extend(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    offsets.append(len(pdf))
    pdf.extend(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n")

    offsets.append(len(pdf))
    pdf.extend(f"4 0 obj\n<< /Length {len(content)} >>\nstream\n".encode("latin1") + content + b"endstream\nendobj\n")

    offsets.append(len(pdf))
    pdf.extend(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for off in offsets:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin1"))

    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin1"))
    return bytes(pdf)


def _create_multi_page_pdf(pages_spec, tmp_path, filename="doc.pdf"):
    """Create a multi-page PDF where each page can be native text, image-only, or garbage text.
    pages_spec is a list of tuples: ("native" | "image" | "garbage", text_content)
    """
    writer = pypdf.PdfWriter()

    for kind, text in pages_spec:
        if kind in ("native", "garbage"):
            pdf_bytes = _create_native_pdf_bytes(text)
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            writer.add_page(reader.pages[0])
        elif kind == "image":
            img_bytes = _create_image_pdf(text)
            reader = pypdf.PdfReader(io.BytesIO(img_bytes))
            writer.add_page(reader.pages[0])

    path = tmp_path / filename
    with open(path, "wb") as f:
        writer.write(f)
    return str(path)


# ---------------------------------------------------------------------------
# 1. Text Quality & Garbage Detection Heuristics
# ---------------------------------------------------------------------------

def test_quality_heuristic_valid_compliance_text():
    """Conservative test: Valid compliance prose with codes, dates, tables must be accepted."""
    samples = [
        "Policy No: 116N216V01. Bajaj Life Secure Plus.",
        "Maturity Benefit: 100% of Sum Assured payable on survival till 23/09/2045.",
        "Disclaimers: Insurance is the subject matter of solicitation. IRDAI Regn No. 116.",
        "Table 2.1: Premium Rates (Annualized Premium = Rs. 50,000/- per annum).",
        "Section 45 of the Insurance Act, 1938 as amended from time to time.",
    ]
    for sample in samples:
        assert is_text_usable(sample) is True
        assert is_page_text_usable([sample]) is True


def test_quality_heuristic_garbage_and_cid():
    """Garbage text: CID codes, replacement chars, or corrupted symbols must be detected."""
    # CID font corruption
    assert is_text_usable("(cid:12) (cid:14) (cid:15) (cid:16) (cid:20)") is False
    # Unicode replacement chars
    assert is_text_usable("\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd") is False
    # Non-alphanumeric noise
    assert is_text_usable("!@#$%^&*()_+{}[]:;<>?/~`") is False
    # Empty / whitespace
    assert is_text_usable("") is False
    assert is_text_usable("   \n\t  ") is False


# ---------------------------------------------------------------------------
# 2. OCR Bounding Box & Coordinate Conversion
# ---------------------------------------------------------------------------

def test_ocr_bbox_coordinate_conversion():
    """Pixel coordinates on rendered image convert accurately to PDF points."""
    # Assume rendered image: 1600x1200 px, PDF page size: 800x600 pt (2.0 scale)
    px_bbox = (100.0, 200.0, 500.0, 400.0)
    pdf_bbox = _convert_bbox_to_pdf_points(px_bbox, 1600.0, 1200.0, 800.0, 600.0)
    assert pdf_bbox == (50.0, 100.0, 250.0, 200.0)


def test_split_line_into_words_proportional_bbox():
    """Line text splits into words with proportional horizontal bounding boxes."""
    line_text = "Maturity Benefit 100%"
    line_bbox = (50.0, 100.0, 250.0, 120.0)  # width = 200 pt
    words = _split_line_into_words(line_text, line_bbox, 0.95, page=1)

    assert len(words) == 3
    assert words[0].text == "Maturity"
    assert words[1].text == "Benefit"
    assert words[2].text == "100%"
    assert words[0].page == 1
    assert words[0].confidence == 0.95
    assert words[0].x0 == 50.0
    assert words[2].x1 == 250.0
    assert words[0].y0 == 100.0 and words[0].y1 == 120.0


# ---------------------------------------------------------------------------
# 3. Normal Text PDF (No OCR needed)
# ---------------------------------------------------------------------------

def test_normal_text_pdf_native_extraction(tmp_path):
    pdf = _create_multi_page_pdf(
        [("native", "This is a clean native PDF with normal text.")],
        tmp_path,
        "clean.pdf"
    )
    # Ensure native extraction works and does not invoke OCR
    with patch("app.services.comparison_ocr_service.ocr_page_to_lines") as mock_ocr:
        segments = extract_pdf_segments(pdf)
        assert len(segments) == 1
        assert "clean native PDF" in segments[0]
        assert mock_ocr.call_count == 0


# ---------------------------------------------------------------------------
# 4. Fully Scanned PDF with PaddleOCR
# ---------------------------------------------------------------------------

def test_fully_scanned_pdf_paddle_ocr(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [("image", "SCANNED CLAUSE 42 GUARANTEED BENEFIT")],
        tmp_path,
        "scanned.pdf"
    )

    # Stub PaddleOCR adapter output
    mock_words = [
        OCRWord("SCANNED", 0.98, 1, 40.0, 100.0, 120.0, 120.0),
        OCRWord("CLAUSE", 0.97, 1, 130.0, 100.0, 200.0, 120.0),
        OCRWord("42", 0.99, 1, 210.0, 100.0, 240.0, 120.0),
        OCRWord("GUARANTEED", 0.96, 1, 250.0, 100.0, 360.0, 120.0),
        OCRWord("BENEFIT", 0.95, 1, 370.0, 100.0, 450.0, 120.0),
    ]

    monkeypatch.setattr(
        "app.services.comparison_ocr_service._run_paddle_ocr",
        lambda pil, page, pdf_w, pdf_h: mock_words,
    )
    monkeypatch.setattr(settings, "compare_ocr_engine", "paddle")

    segments = extract_pdf_segments(pdf)
    assert any("GUARANTEED BENEFIT" in s for s in segments)


# ---------------------------------------------------------------------------
# 5. Mixed PDF (Page 1 native, Page 2 OCR, Page 3 native)
# ---------------------------------------------------------------------------

def test_mixed_pdf_only_ocrs_unusable_page(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [
            ("native", "Page 1: Normal native text layer."),
            ("image", "Page 2: Scanned image with no text layer."),
            ("native", "Page 3: Normal native text layer."),
        ],
        tmp_path,
        "mixed.pdf"
    )

    ocr_called_pages = []

    def stub_ocr(pil, page, pdf_w, pdf_h):
        ocr_called_pages.append(page)
        return [
            OCRWord("Page", 0.9, page, 10, 10, 50, 20),
            OCRWord("2:", 0.9, page, 55, 10, 75, 20),
            OCRWord("Scanned", 0.9, page, 80, 10, 140, 20),
            OCRWord("recovered.", 0.9, page, 145, 10, 220, 20),
        ]

    monkeypatch.setattr("app.services.comparison_ocr_service._run_paddle_ocr", stub_ocr)
    monkeypatch.setattr(settings, "compare_ocr_engine", "paddle")

    segments = extract_pdf_segments(pdf)
    # Verify ONLY page 2 triggered OCR
    assert ocr_called_pages == [2]
    assert any("Page 1:" in s for s in segments)
    assert any("Scanned recovered." in s for s in segments)
    assert any("Page 3:" in s for s in segments)


# ---------------------------------------------------------------------------
# 6. Garbage Text Layer ((cid:...) triggers OCR)
# ---------------------------------------------------------------------------

def test_garbage_text_layer_triggers_ocr(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [("garbage", "(cid:10) (cid:15) (cid:20) (cid:25) (cid:30) (cid:35) (cid:40)")],
        tmp_path,
        "garbage.pdf"
    )

    ocr_triggered = []

    def stub_ocr(pil, page, pdf_w, pdf_h):
        ocr_triggered.append(page)
        return [
            OCRWord("Recovered", 0.95, page, 20, 50, 100, 70),
            OCRWord("Text", 0.95, page, 105, 50, 150, 70),
            OCRWord("Clause.", 0.95, page, 155, 50, 210, 70),
        ]

    monkeypatch.setattr("app.services.comparison_ocr_service._run_paddle_ocr", stub_ocr)
    monkeypatch.setattr(settings, "compare_ocr_engine", "paddle")

    segments = extract_pdf_segments(pdf)
    assert ocr_triggered == [1]
    assert any("Recovered Text Clause." in s for s in segments)


# ---------------------------------------------------------------------------
# 7. Tesseract Fallback When PaddleOCR Fails
# ---------------------------------------------------------------------------

def test_tesseract_fallback_on_paddle_failure(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [("image", "FALLBACK CONTENT")],
        tmp_path,
        "fallback.pdf"
    )

    def failing_paddle(pil, page, pdf_w, pdf_h):
        raise RuntimeError("PaddleOCR CUDA/Engine init error")

    def working_tesseract(pil, page, pdf_w, pdf_h):
        return [
            OCRWord("Tesseract", 0.85, page, 20, 50, 100, 70),
            OCRWord("Fallback", 0.85, page, 105, 50, 180, 70),
            OCRWord("Active.", 0.85, page, 185, 50, 230, 70),
        ]

    monkeypatch.setattr("app.services.comparison_ocr_service._run_paddle_ocr", failing_paddle)
    monkeypatch.setattr("app.services.comparison_ocr_service._run_tesseract_ocr", working_tesseract)
    monkeypatch.setattr(settings, "compare_ocr_engine", "paddle")

    segments = extract_pdf_segments(pdf)
    assert any("Tesseract Fallback Active." in s for s in segments)


# ---------------------------------------------------------------------------
# 8. OCR Disabled Behavior (COMPARE_OCR_ENABLED=False)
# ---------------------------------------------------------------------------

def test_ocr_disabled_fails_gracefully(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [("image", "SCANNED ONLY")],
        tmp_path,
        "disabled.pdf"
    )

    monkeypatch.setattr(settings, "compare_ocr_enabled", False)

    with pytest.raises(ValueError) as exc:
        extract_pdf_segments(pdf)
    assert "scanned image" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 9. Pixel Comparison & Positioned Words with OCR
# ---------------------------------------------------------------------------

def test_positioned_words_and_render_pair_with_ocr(tmp_path, monkeypatch):
    old_pdf = _create_multi_page_pdf(
        [("image", "Annual Premium Rs 50000")],
        tmp_path,
        "old_scanned.pdf"
    )
    new_pdf = _create_multi_page_pdf(
        [("image", "Monthly Premium Rs 5000")],
        tmp_path,
        "new_scanned.pdf"
    )

    def stub_ocr_words(pil, page, pdf_w, pdf_h):
        return [
            OCRWord("Premium", 0.95, page, 50.0, 100.0, 120.0, 115.0),
            OCRWord("Amount", 0.95, page, 125.0, 100.0, 180.0, 115.0),
        ]

    monkeypatch.setattr("app.services.comparison_ocr_service.ocr_page_to_words", stub_ocr_words)
    monkeypatch.setattr(settings, "compare_ocr_engine", "paddle")

    words = positioned_words(old_pdf)
    assert len(words) == 2
    assert words[0].text == "Premium"
    assert words[0].x0 == 50.0 and words[0].y0 == 100.0

    # Test _render_pair overlay assembly
    from app.services import render_orchestrator as ro
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path / "up"))

    res = _render_pair("ocr-test-cmp", old_pdf, new_pdf)
    assert "old" in res and "new" in res and "changes" in res
    assert len(res["old"]["pages"]) == 1
    assert len(res["new"]["pages"]) == 1


# ---------------------------------------------------------------------------
# 10. Positioned Search Across OCR Pages
# ---------------------------------------------------------------------------

def test_search_pdf_on_scanned_ocr_page(tmp_path, monkeypatch):
    pdf = _create_multi_page_pdf(
        [("image", "Guaranteed Surrender Value Clause")],
        tmp_path,
        "search_scanned.pdf"
    )

    def stub_ocr_words(pil, page, pdf_w, pdf_h):
        return [
            OCRWord("Guaranteed", 0.95, page, 50.0, 100.0, 130.0, 115.0),
            OCRWord("Surrender", 0.95, page, 135.0, 100.0, 200.0, 115.0),
            OCRWord("Value", 0.95, page, 205.0, 100.0, 250.0, 115.0),
        ]

    monkeypatch.setattr("app.services.comparison_ocr_service.ocr_page_to_words", stub_ocr_words)

    hits = _search_pdf(pdf, "Surrender Value")
    assert len(hits) == 1
    assert hits[0]["page"] == 1
    assert hits[0]["bbox"] == [135.0, 100.0, 250.0, 115.0]
