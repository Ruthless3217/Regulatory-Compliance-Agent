"""PDF -> HTML import: structure that is really there, and nothing more.

A PDF carries no semantic structure, so these tests pin the two failure modes
that matter: physical lines must not each become their own paragraph (a
document nobody can edit), and a short body line must not be promoted to a
heading on text shape alone (invented structure).
"""
import io
import uuid

import pytest

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.models.submission import Submission  # noqa: E402
from app.services import lexical_document_service as svc  # noqa: E402
from app.services.lexical_import import LexicalImportError, pdf_to_html  # noqa: E402

BODY = "Helvetica"
BOLD = "Helvetica-Bold"


def _pdf(pages) -> bytes:
    """Build a PDF. `pages` is a list of pages; a page is a list of blocks;
    a block is (font, size, [lines]) laid out with a blank line between blocks."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    for page in pages:
        y = height - 72
        for font, size, lines in page:
            c.setFont(font, size)
            for line in lines:
                c.drawString(72, y, line)
                y -= size + 3
            y -= size + 12  # deliberate block gap
        c.showPage()
    c.save()
    return buf.getvalue()


def test_wrapped_lines_join_into_one_paragraph_per_block():
    html = pdf_to_html(_pdf([[
        (BODY, 11, [
            "This plan levies a premium allocation charge on each",
            "premium paid during the first five policy years.",
        ]),
        (BODY, 11, [
            "Fund management charges are levied daily as a share",
            "of the fund value.",
        ]),
    ]]))

    assert "<p>This plan levies a premium allocation charge on each premium " \
           "paid during the first five policy years.</p>" in html
    assert "<p>Fund management charges are levied daily as a share of the " \
           "fund value.</p>" in html


def test_larger_font_short_line_becomes_a_heading():
    html = pdf_to_html(_pdf([[
        (BOLD, 18, ["Policy Charges"]),
        (BODY, 11, [
            "This plan levies a premium allocation charge on each",
            "premium paid during the first five policy years.",
        ]),
    ]]))

    assert "<h2>Policy Charges</h2>" in html


def test_same_size_bold_short_line_becomes_a_heading():
    html = pdf_to_html(_pdf([[
        (BOLD, 11, ["Policy Charges"]),
        (BODY, 11, [
            "This plan levies a premium allocation charge on each",
            "premium paid during the first five policy years.",
        ]),
    ]]))

    assert "<h2>Policy Charges</h2>" in html


def test_heading_tight_against_the_text_below_it_still_becomes_a_heading():
    """A heading is usually set close to the copy it introduces, so it must not
    need a paragraph-sized gap under it to be recognised."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 72
    c.setFont(BOLD, 13)
    c.drawString(72, y, "Key Benefits")
    y -= 16  # tight: ordinary line leading, no block gap
    c.setFont(BODY, 11)
    for line in [
        "Guaranteed maturity benefit of up to 110 percent of all",
        "premiums paid, plus life cover through the policy term.",
    ]:
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.save()

    html = pdf_to_html(buf.getvalue())

    assert "<h2>Key Benefits</h2>" in html
    assert "<p>Guaranteed maturity benefit" in html


def test_bold_table_header_row_is_not_promoted_to_a_heading():
    """A bold table header row reads exactly like a heading in plain text. The
    column gutters are the evidence that it is a row of cells, not a title."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 72
    c.setFont(BOLD, 11)
    for x, head in ((72, "Policy year"), (220, "Allocation charge")):
        c.drawString(x, y, head)
    y -= 15
    c.setFont(BODY, 11)
    for x, cell in ((72, "1-5"), (220, "6.0%")):
        c.drawString(x, y, cell)
    c.showPage()
    c.save()

    html = pdf_to_html(buf.getvalue())

    assert "<h2>" not in html
    assert "Policy year" in html and "Allocation charge" in html


def test_short_body_line_is_not_promoted_to_a_heading():
    """Heading-shaped text with no typographic evidence stays a paragraph.
    A flat document is better than an invented outline."""
    html = pdf_to_html(_pdf([[
        (BODY, 11, [
            "This plan levies a premium allocation charge on each",
            "premium paid during the first five policy years.",
        ]),
        (BODY, 11, ["Fund value grew"]),
    ]]))

    assert "<p>Fund value grew</p>" in html
    assert "<h2>" not in html


def test_running_headers_and_page_numbers_are_dropped():
    # Body text differs per page: text repeated on every page is a running line
    # by definition, so only the chrome may be identical here.
    pages = [
        [
            (BODY, 9, ["Acme Life Confidential"]),
            (BODY, 11, [
                f"Section {n} explains how the allocation charge is",
                f"deducted in policy year {n} before units are bought.",
            ]),
            (BODY, 9, [f"Page {n}"]),
        ]
        for n in (1, 2, 3)
    ]
    html = pdf_to_html(_pdf(pages))

    assert "Acme Life Confidential" not in html
    assert "Page 1" not in html and "Page 2" not in html
    assert "Section 1 explains" in html and "Section 3 explains" in html


def test_hyphenated_line_wrap_is_rejoined():
    html = pdf_to_html(_pdf([[
        (BODY, 11, [
            "The policyholder is entitled to surrender the insur-",
            "ance policy after the lock-in period ends.",
        ]),
    ]]))

    assert "insurance policy" in html


def test_markup_in_the_pdf_text_is_escaped():
    html = pdf_to_html(_pdf([[
        (BODY, 11, [
            "Returns of 8% <b>guaranteed</b> & tax free for every",
            "policyholder who stays invested to maturity.",
        ]),
    ]]))

    assert "&lt;b&gt;" in html and "&amp;" in html
    assert "<b>" not in html


@pytest.mark.parametrize("data", [b"", b"not a pdf at all", b"%PDF-1.4 truncated"])
def test_unreadable_pdf_raises_lexical_import_error(data):
    with pytest.raises(LexicalImportError):
        pdf_to_html(data)


def test_pdf_with_no_text_layer_raises_lexical_import_error():
    """A scanned PDF has no text to edit; the caller keeps extracted text."""
    with pytest.raises(LexicalImportError):
        pdf_to_html(_pdf([[]]))


# --- routing through the submission service --------------------------------


def test_pdf_submission_gets_a_working_document(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf([[
        (BOLD, 18, ["Policy Charges"]),
        (BODY, 11, [
            "This plan levies a premium allocation charge on each",
            "premium paid during the first five policy years.",
        ]),
    ]]))
    before = path.read_bytes()

    sub = Submission(id=uuid.uuid4(), title="t", content_type="pdf",
                     file_path=str(path))
    html = svc.build_import_html(sub)

    assert "<h2>Policy Charges</h2>" in html
    assert "<p>" in html
    assert path.read_bytes() == before, "the upload is immutable"


def test_corrupt_pdf_submission_returns_none(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf at all")

    sub = Submission(id=uuid.uuid4(), title="t", content_type="pdf",
                     file_path=str(path))
    assert svc.build_import_html(sub) is None


def test_missing_pdf_file_returns_none(tmp_path):
    sub = Submission(id=uuid.uuid4(), title="t", content_type="pdf",
                     file_path=str(tmp_path / "absent.pdf"))
    assert svc.build_import_html(sub) is None


def test_docx_still_routes_to_the_docx_path(tmp_path, monkeypatch):
    """Adding the PDF branch must not divert DOCX through it."""
    from docx import Document

    monkeypatch.setattr(
        "app.services.lexical_document_service.pdf_to_html",
        lambda _: pytest.fail("docx must not go through the PDF path"),
    )

    path = tmp_path / "a.docx"
    doc = Document()
    doc.add_heading("Charges", level=2)
    buf = io.BytesIO()
    doc.save(buf)
    path.write_bytes(buf.getvalue())

    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(path))
    assert "<h2>" in svc.build_import_html(sub)
