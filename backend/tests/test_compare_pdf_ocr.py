"""OCR fallback for scanned/image PDFs in the Compare tool.

A scanned PDF has no selectable text layer, so extract_pdf_segments must render
its pages and OCR them (Tesseract via pytesseract) instead of failing. These
tests stub the OCR engine so they run without the tesseract binary; they verify
the branching and the graceful message, not Tesseract's accuracy.
"""
import io
import sys
import types

import img2pdf
import pytest
from PIL import Image, ImageDraw

from app.services.comparison_service import extract_pdf_segments, _pdf_text_layer_lines


def _image_only_pdf(tmp_path, text="SCANNED Maturity Benefit clause 42"):
    """A PDF whose only content is a raster image — no text layer."""
    img = Image.new("RGB", (1000, 300), (255, 255, 255))
    ImageDraw.Draw(img).text((40, 130), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    path = tmp_path / "scanned.pdf"
    path.write_bytes(img2pdf.convert(buf.getvalue()))
    return str(path)


def _stub_pytesseract(monkeypatch, returns):
    mod = types.ModuleType("pytesseract")
    mod.image_to_string = lambda pil, *a, **k: returns
    monkeypatch.setitem(sys.modules, "pytesseract", mod)


def test_scanned_pdf_has_no_text_layer(tmp_path):
    # Sanity: the fixture really is image-only, so the OCR branch is exercised.
    assert not any(_pdf_text_layer_lines(_image_only_pdf(tmp_path)))


def test_scanned_pdf_ocr_fallback_recovers_text(tmp_path, monkeypatch):
    pdf = _image_only_pdf(tmp_path)
    _stub_pytesseract(monkeypatch, "SCANNED Maturity Benefit clause 42.")
    segments = extract_pdf_segments(pdf)
    assert any("Maturity Benefit" in s for s in segments)


def test_scanned_pdf_without_engine_gives_clear_message(tmp_path, monkeypatch):
    pdf = _image_only_pdf(tmp_path)
    # Simulate the tesseract tooling being absent: importing pytesseract fails.
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    with pytest.raises(ValueError) as ei:
        extract_pdf_segments(pdf)
    assert "scanned image" in str(ei.value).lower()
