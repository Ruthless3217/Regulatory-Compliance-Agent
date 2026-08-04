"""Export is generated from the working document, not from the upload.

The uploaded file is the immutable original; the approved artifact is this.
Structure must survive the round trip or the editor's fidelity is pointless.

The upload is also the *template*: "put a docx in, get the same docx back"
means the export clones the original's styles, page setup, headers and footers
rather than building a default-styled Word file from scratch.
"""
import io

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Inches

from app.services.lexical_export import lexical_html_to_docx

# 1x1 transparent PNG — the smallest thing python-docx will accept as a picture.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


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


# ---------------------------------------------------------------------------
# Inline fidelity — the character-level formatting the editor shows
# ---------------------------------------------------------------------------

def _first(data: bytes, index: int = 0):
    return [p for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()][index]


def test_inline_markup_does_not_glue_words_together():
    """The reviewer's sentence must survive intact.

    An inline tag splits a paragraph into several strings; joining them without
    their spacing turns "Hello world now" into "Helloworldnow" — the document
    reads as corrupted even though every character is present.
    """
    data = lexical_html_to_docx("<p>Hello <strong>world</strong> now.</p>", "t")
    assert _first(data).text == "Hello world now."


def test_bold_italic_and_underline_survive_as_runs():
    html = "<p>plain <b>bold</b> <i>italic</i> <u>under</u></p>"
    runs = {r.text: r for r in _first(lexical_html_to_docx(html, "t")).runs if r.text.strip()}
    assert runs["bold"].bold is True
    assert runs["italic"].italic is True
    assert runs["under"].underline is True
    assert runs["plain "].bold is not True


def test_nested_emphasis_keeps_both_formats():
    html = "<p><strong>bold and <em>also italic</em></strong></p>"
    run = next(r for r in _first(lexical_html_to_docx(html, "t")).runs if "italic" in r.text)
    assert run.bold is True and run.italic is True


def test_line_break_stays_a_break():
    data = lexical_html_to_docx("<p>one<br/>two</p>", "t")
    assert _first(data).text == "one\ntwo"


def test_paragraph_alignment_survives():
    data = lexical_html_to_docx('<p style="text-align: center">Centred.</p>', "t")
    assert _first(data).alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_nested_list_keeps_its_level():
    html = "<ul><li>outer<ul><li>inner</li></ul></li></ul>"
    doc = Document(io.BytesIO(lexical_html_to_docx(html, "t")))
    styles = {p.text: p.style.name for p in doc.paragraphs if p.text.strip()}
    assert styles["outer"] == "List Bullet"
    assert styles["inner"] == "List Bullet 2"


def test_heading_keeps_its_inline_formatting():
    data = lexical_html_to_docx("<h2>Key <em>charges</em></h2>", "t")
    para = _first(data)
    assert para.text == "Key charges"
    assert next(r for r in para.runs if "charges" in r.text).italic is True


def test_table_cell_keeps_inline_formatting_and_spacing():
    html = "<table><tr><td>fund <b>value</b></td></tr></table>"
    cell = Document(io.BytesIO(lexical_html_to_docx(html, "t"))).tables[0].rows[0].cells[0]
    assert cell.text == "fund value"
    assert next(r for r in cell.paragraphs[0].runs if r.text == "value").bold is True


# ---------------------------------------------------------------------------
# Template cloning — the upload's own styling is the export's styling
# ---------------------------------------------------------------------------

def _template(tmp_path) -> str:
    """A DOCX with everything a default `Document()` would throw away."""
    doc = Document()

    heading2 = doc.styles["Heading 2"]
    heading2.font.name = "Garamond"
    heading2.font.size = Pt(20)
    doc.styles.add_style("Disclaimer", WD_STYLE_TYPE.PARAGRAPH)

    section = doc.sections[0]
    # Whole/half inches only: Word stores these in twips, so 5.83" would not
    # compare equal to itself after a save/load round trip.
    section.page_width, section.page_height = Inches(5.5), Inches(8.5)
    section.left_margin = section.right_margin = Inches(1.75)

    section.header.paragraphs[0].text = "Bajaj Allianz Life"
    section.footer.paragraphs[0].text = "Insurance is the subject matter of solicitation."

    doc.add_heading("OLD TEMPLATE HEADING", level=2)
    doc.add_paragraph("OLD TEMPLATE BODY")
    doc.add_table(rows=1, cols=2).rows[0].cells[0].text = "OLD TEMPLATE TABLE"

    path = tmp_path / "upload.docx"
    doc.save(str(path))
    return str(path)


def _exported(tmp_path, html: str) -> Document:
    return Document(io.BytesIO(lexical_html_to_docx(html, "t", _template(tmp_path))))


def test_template_custom_styles_survive_the_round_trip(tmp_path):
    """The point of the whole feature: the export looks like the upload."""
    out = _exported(tmp_path, "<h2>Charges</h2><p>Body copy.</p>")
    assert out.styles["Heading 2"].font.name == "Garamond"
    assert out.styles["Heading 2"].font.size == Pt(20)
    assert "Disclaimer" in [s.name for s in out.styles]
    assert ("Charges", "Heading 2") in {(p.text, p.style.name) for p in out.paragraphs if p.text}


def test_template_body_content_is_replaced_not_appended(tmp_path):
    out = _exported(tmp_path, "<p>New approved copy.</p>")
    body = "\n".join(p.text for p in out.paragraphs)
    assert "New approved copy." in body
    assert "OLD TEMPLATE BODY" not in body
    assert "OLD TEMPLATE HEADING" not in body
    assert out.tables == []


def test_template_page_setup_survives_because_sectpr_is_kept(tmp_path):
    out = _exported(tmp_path, "<p>New approved copy.</p>")
    section = out.sections[0]
    assert (section.page_width, section.page_height) == (Inches(5.5), Inches(8.5))
    assert section.left_margin == Inches(1.75)
    # sectPr must be the *last* child of the body, or Word repairs the file.
    assert out.element.body[-1].tag.endswith("}sectPr")


def test_labelled_footer_goes_back_to_the_footer_not_the_body(tmp_path):
    """lexical_import appends the footer to the body so it can be reviewed and
    corrected. Writing it back as body text would relocate a mandated
    disclaimer out of the footer of the approved document."""
    html = (
        "<p>Body copy.</p>"
        "<h3>Page footer</h3><p>Insurance is the subject matter of solicitation. v2</p>"
    )
    out = _exported(tmp_path, html)
    assert "v2" in "\n".join(p.text for p in out.sections[0].footer.paragraphs)
    assert "v2" not in "\n".join(p.text for p in out.paragraphs)


def test_footer_text_keeps_its_spacing(tmp_path):
    """The footer carries the mandated disclaimer — it goes back word for word."""
    html = "<p>Body.</p><h3>Page footer</h3><p>Insurance is the <b>subject</b> matter.</p>"
    out = _exported(tmp_path, html)
    assert "Insurance is the subject matter." in "\n".join(
        p.text for p in out.sections[0].footer.paragraphs
    )


def test_labelled_header_goes_back_to_the_header(tmp_path):
    html = "<p>Body copy.</p><h3>Page header</h3><p>Bajaj Allianz Life Insurance</p>"
    out = _exported(tmp_path, html)
    assert "Bajaj Allianz Life Insurance" in "\n".join(
        p.text for p in out.sections[0].header.paragraphs
    )
    assert "Bajaj Allianz Life Insurance" not in "\n".join(p.text for p in out.paragraphs)


def test_unmappable_label_leaves_the_original_footer_alone(tmp_path):
    """A footer labelled for a section that does not exist is dropped, never
    duplicated into the body."""
    html = "<p>Body copy.</p><h3>Page footer 9</h3><p>Orphaned disclaimer.</p>"
    out = _exported(tmp_path, html)
    assert "Orphaned disclaimer." not in "\n".join(p.text for p in out.paragraphs)
    footer = "\n".join(p.text for p in out.sections[0].footer.paragraphs)
    assert footer == "Insurance is the subject matter of solicitation."


def test_base64_image_is_embedded(tmp_path):
    html = f'<p><img src="data:image/png;base64,{_PNG_B64}" /></p>'
    out = _exported(tmp_path, html)
    assert len(out.inline_shapes) == 1


def test_unreadable_image_is_skipped_not_fatal(tmp_path):
    html = '<p><img src="https://example.com/logo.png" />Body copy.</p><p><img /></p>'
    out = _exported(tmp_path, html)
    assert "Body copy." in "\n".join(p.text for p in out.paragraphs)
    assert len(out.inline_shapes) == 0


def test_missing_template_falls_back_to_a_default_document(tmp_path):
    data = lexical_html_to_docx("<h2>Charges</h2>", "t", str(tmp_path / "gone.docx"))
    assert "Charges" in _paragraphs(data)


def test_no_template_still_produces_a_valid_document():
    data = lexical_html_to_docx("<h2>Charges</h2><p>Body copy.</p>", "t", None)
    assert data[:2] == b"PK"
    assert ("Charges", "Heading 2") in {
        (p.text, p.style.name) for p in Document(io.BytesIO(data)).paragraphs if p.text
    }


def test_template_file_is_byte_identical_after_export(tmp_path):
    """The upload is immutable. It is opened read-only and cloned in memory."""
    path = _template(tmp_path)
    with open(path, "rb") as f:
        before = f.read()
    lexical_html_to_docx("<h2>New</h2><h3>Page footer</h3><p>New footer.</p>", "t", path)
    with open(path, "rb") as f:
        assert f.read() == before
