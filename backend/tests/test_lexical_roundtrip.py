"""DOCX -> HTML -> DOCX must preserve structure end to end.

lexical_import and lexical_export are tested separately against hand-written
HTML, and they agreed on an idealised shape that mammoth does not actually
emit: real cell markup is `<td><p>a</p></td>`, not `<td>a</td>`. A test that
only ever sees hand-written HTML cannot catch the two halves drifting apart,
so this one feeds the importer's genuine output straight into the exporter.
"""
import io

from docx import Document

from app.services.lexical_export import lexical_html_to_docx
from app.services.lexical_import import docx_to_html


def _brochure() -> bytes:
    doc = Document()
    doc.add_heading("Charges", level=2)
    doc.add_paragraph("Returns are not guaranteed.")
    doc.add_paragraph("first", style="List Bullet")
    doc.add_paragraph("second", style="List Bullet")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Premium"
    table.rows[0].cells[1].text = "12,000"
    table.rows[1].cells[0].text = "Term"
    table.rows[1].cells[1].text = "10 yrs"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _round_trip() -> Document:
    html = docx_to_html(_brochure())
    return Document(io.BytesIO(lexical_html_to_docx(html, "Brochure")))


def test_heading_survives_the_round_trip():
    out = _round_trip()
    styled = {(p.text, p.style.name) for p in out.paragraphs if p.text.strip()}
    assert ("Charges", "Heading 2") in styled


def test_body_paragraph_survives():
    out = _round_trip()
    assert any(p.text == "Returns are not guaranteed." for p in out.paragraphs)


def test_bullets_survive_as_list_paragraphs():
    out = _round_trip()
    bullets = [p.text for p in out.paragraphs if p.style.name == "List Bullet"]
    assert bullets == ["first", "second"]


def test_table_survives_mammoths_nested_cell_markup():
    """The specific divergence: mammoth wraps cell text in <p>."""
    out = _round_trip()
    assert len(out.tables) == 1
    rows = [[c.text for c in r.cells] for r in out.tables[0].rows]
    assert rows == [["Premium", "12,000"], ["Term", "10 yrs"]]


def test_importer_really_does_nest_cell_content():
    """Pins the assumption the exporter has to tolerate, so a future mammoth
    upgrade that changes it fails here rather than silently emptying tables."""
    html = docx_to_html(_brochure())
    assert "<td><p>" in html
