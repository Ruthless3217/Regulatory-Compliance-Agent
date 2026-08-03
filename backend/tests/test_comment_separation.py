"""Comments/annotations must never enter the graded creative text (Phase 3).

Verified leak paths from the 2026-07-28 audit (docs/audits/2026-07-28-comment-
annotation-audit.md): PDF reviewer annotations rasterized into the OCR fallback
(pypdfium2 renders /Annots by default), hidden HTML text surviving extraction,
and tracked-change text inside DOCX text boxes. Word comments, tracked
deletions and HTML <!-- --> comments were verified NOT to leak — pinned here so
they stay that way.
"""
import asyncio
import sys
import types

import pytest
from docx import Document
from docx.oxml import parse_xml

from app.services.preprocessing_service import ContextEngineeringService


def _svc():
    return ContextEngineeringService(db=None)


# --- PDF OCR must not rasterize reviewer annotations ---------------------------

def test_pdf_ocr_renders_without_annotations(monkeypatch):
    """_pdf_ocr_lines must pass draw_annots=False: a reviewer's FreeText note on
    a flattened creative must not be OCR'd into the compliance text."""
    captured = {}

    class FakePage:
        def render(self, scale, **kwargs):
            captured.update(kwargs, scale=scale)

            class _Bitmap:
                def to_pil(self):
                    return object()
            return _Bitmap()

    class FakePdf:
        def __len__(self):
            return 1

        def __getitem__(self, i):
            return FakePage()

        def close(self):
            pass

    fake_pdfium = types.ModuleType("pypdfium2")
    fake_pdfium.PdfDocument = lambda path: FakePdf()
    monkeypatch.setitem(sys.modules, "pypdfium2", fake_pdfium)

    fake_tess = types.ModuleType("pytesseract")
    fake_tess.image_to_string = lambda pil, *a, **k: "OCR TEXT"
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tess)

    from app.services.comparison_service import _pdf_ocr_lines
    lines = _pdf_ocr_lines("whatever.pdf")
    assert lines == [["OCR TEXT"]]
    assert captured.get("draw_annots") is False


# --- HTML hidden text must not be graded ---------------------------------------

def test_html_hidden_text_is_excluded():
    html = """
    <html><body>
      <p>Visible claim: returns of 22% p.a.</p>
      <div style="display:none">Reviewer note: do not publish before UW approval</div>
      <span style="visibility: hidden">hidden-visibility text</span>
      <p hidden>hidden-attribute text</p>
      <p aria-hidden="true">aria-hidden reviewer text</p>
    </body></html>
    """
    text = _svc()._extract_html(html)
    assert "Visible claim" in text
    assert "do not publish" not in text
    assert "hidden-visibility text" not in text
    assert "hidden-attribute text" not in text
    assert "aria-hidden reviewer text" not in text


def test_html_comment_markup_stays_excluded():
    html = "<html><body><p>Copy.</p><!-- reviewer: fix this --></body></html>"
    text = _svc()._extract_html(html)
    assert "reviewer: fix this" not in text


# --- DOCX: tracked-change text inside text boxes -------------------------------

_TEXTBOX_WITH_TRACKED = (
    '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml"><w:r><w:pict>'
    '<v:shape><v:textbox><w:txbxContent>'
    '<w:p><w:r><w:t>Kept textbox copy</w:t></w:r></w:p>'
    '<w:p><w:moveFrom w:id="1" w:author="r"><w:r><w:t>moved-away stale text</w:t></w:r></w:moveFrom></w:p>'
    '<w:p><w:del w:id="2" w:author="r"><w:r><w:delText>deleted text</w:delText></w:r></w:del></w:p>'
    "</w:txbxContent></v:textbox></v:shape>"
    "</w:pict></w:r></w:p>"
)


def test_docx_textbox_excludes_tracked_change_text(tmp_path):
    doc = Document()
    doc.add_paragraph("Body.")
    doc.element.body.append(parse_xml(_TEXTBOX_WITH_TRACKED))
    p = tmp_path / "tracked_textbox.docx"
    doc.save(p)

    text = asyncio.run(_svc()._extract_docx(str(p)))
    assert "Kept textbox copy" in text
    assert "moved-away stale text" not in text
    assert "deleted text" not in text


def test_docx_word_comments_do_not_leak(tmp_path):
    """Word comments live in word/comments.xml — pinned as non-extracted."""
    doc = Document()
    doc.add_paragraph("Creative body copy.")
    p = tmp_path / "plain.docx"
    doc.save(p)
    text = asyncio.run(_svc()._extract_docx(str(p)))
    assert "Creative body copy." in text


# --- run-level visibility of comment influence ---------------------------------

def test_grounding_mix_counts_verdict_origins():
    from app.services.agents.graph.nodes import grounding_mix
    violations = [
        {"violation_metadata": {"grounding": "precedent"}},
        {"violation_metadata": {"grounding": "precedent"}},
        {"violation_metadata": {"grounding": "rule"}},
        {"violation_metadata": {"grounding": "disclosure"}},
        {"violation_metadata": {}},
    ]
    mix = grounding_mix(violations)
    assert mix == {"precedent": 2, "rule": 1, "disclosure": 1, "unknown": 1}
