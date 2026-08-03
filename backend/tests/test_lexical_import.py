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
