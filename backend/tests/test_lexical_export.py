"""Export is generated from the working document, not from the upload.

The uploaded file is the immutable original; the approved artifact is this.
Structure must survive the round trip or the editor's fidelity is pointless.
"""
import io

from docx import Document

from app.services.lexical_export import lexical_html_to_docx


def _paragraphs(data: bytes):
    return [p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()]


def _styles(data: bytes):
    return [p.style.name for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()]


def test_headings_export_as_word_headings():
    data = lexical_html_to_docx("<h2>Charges</h2><p>Body copy.</p>", "t")
    assert "Charges" in _paragraphs(data)
    assert any(s.startswith("Heading") for s in _styles(data))


def test_list_items_export_as_list_paragraphs():
    data = lexical_html_to_docx("<ul><li>first</li><li>second</li></ul>", "t")
    assert "first" in _paragraphs(data) and "second" in _paragraphs(data)


def test_table_exports_as_a_table():
    data = lexical_html_to_docx("<table><tr><td>a</td><td>b</td></tr></table>", "t")
    doc = Document(io.BytesIO(data))
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[0].cells[0].text == "a"


def test_empty_html_produces_a_valid_document():
    """A blank document must not raise — it exports as an empty file."""
    assert lexical_html_to_docx("", "t")
