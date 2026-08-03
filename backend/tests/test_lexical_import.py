"""DOCX -> HTML is the import seam. Structure must survive: headings stay
headings, lists stay lists, tables stay tables. Anything that degrades to a
flat paragraph here is lost to the editor permanently."""
import io

import pytest
from docx import Document

from app.services.lexical_import import LexicalImportError, docx_to_html


def _docx(build) -> bytes:
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_heading_becomes_a_heading_element():
    data = _docx(lambda d: d.add_heading("Charges", level=2))
    html = docx_to_html(data)
    assert "<h2>" in html and "Charges" in html


def test_paragraph_becomes_a_paragraph():
    data = _docx(lambda d: d.add_paragraph("Returns are not guaranteed."))
    assert "<p>" in docx_to_html(data)


def test_bullet_list_becomes_a_list():
    def build(d):
        d.add_paragraph("first", style="List Bullet")
        d.add_paragraph("second", style="List Bullet")

    html = docx_to_html(_docx(build))
    assert "<ul>" in html and html.count("<li>") == 2


def test_table_becomes_a_table():
    def build(d):
        t = d.add_table(rows=1, cols=2)
        t.rows[0].cells[0].text = "a"
        t.rows[0].cells[1].text = "b"

    html = docx_to_html(_docx(build))
    assert "<table>" in html and "<td>" in html


def test_unreadable_input_raises_rather_than_returning_empty():
    with pytest.raises(LexicalImportError):
        docx_to_html(b"this is not a docx")


# --- headers, footers and text boxes ---------------------------------------
#
# mammoth reads the document body only, but preprocessing_service extracts these
# regions too, so the compliance engine grades text the editor would otherwise
# not contain. A finding quoting a footer could not be located or corrected, and
# clean.docx — generated from the editor's HTML — would drop the disclaimer from
# the approved artifact.

def _with_footer(footer_text: str, header_text: str = "") -> bytes:
    doc = Document()
    doc.add_paragraph("Body copy.")
    doc.sections[0].footer.paragraphs[0].text = footer_text
    if header_text:
        doc.sections[0].header.paragraphs[0].text = header_text
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_footer_text_reaches_the_editor():
    html = docx_to_html(_with_footer("Past performance is not indicative of future performance."))
    assert "Past performance is not indicative of future performance." in html
    assert "Page footer" in html


def test_header_text_reaches_the_editor():
    html = docx_to_html(_with_footer("f", header_text="Bajaj Life Smart Wealth Goal VII"))
    assert "Bajaj Life Smart Wealth Goal VII" in html
    assert "Page header" in html


def test_body_still_comes_first():
    """Peripherals are appended, so the document still reads in order."""
    html = docx_to_html(_with_footer("FOOTERTEXT"))
    assert html.index("Body copy.") < html.index("FOOTERTEXT")


def test_document_without_header_or_footer_gains_nothing():
    doc = Document()
    doc.add_paragraph("Body only.")
    buf = io.BytesIO()
    doc.save(buf)
    html = docx_to_html(buf.getvalue())
    assert "Page footer" not in html and "Page header" not in html


def test_peripheral_text_is_escaped_not_injected():
    html = docx_to_html(_with_footer("Terms & <conditions> apply"))
    assert "&amp;" in html and "&lt;conditions&gt;" in html
    assert "<conditions>" not in html
