"""Golden tests for the zero-token structural brochure parser.

Builds tiny synthetic PDFs with reportlab (font-size differences are what the
parser uses to distinguish headings from body text) and parses them with
pdfplumber via `parse_brochure`. See test_lexical_pdf_import.py for the same
fabrication approach used elsewhere in this suite.
"""
import pytest

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.services.brochure_parser import parse_brochure  # noqa: E402

BODY = "Helvetica"
BODY_SIZE = 10
HEADING_SIZE = 16


def _pdf(path, pages):
    """Build a PDF. `pages` is a list of pages; a page is a list of
    (font, size, [lines])."""
    c = canvas.Canvas(str(path), pagesize=letter)
    _, height = letter
    for page in pages:
        y = height - 72
        for font, size, lines in page:
            c.setFont(font, size)
            for line in lines:
                c.drawString(72, y, line)
                y -= size + 6
        c.showPage()
    c.save()


def test_preamble_before_first_heading_survives_as_a_section(tmp_path):
    """A paragraph appearing before the document's first styled heading must
    still reach `sections` (with an empty heading_path) instead of being
    silently dropped from the retrievable corpus."""
    pdf_path = tmp_path / "brochure.pdf"
    _pdf(pdf_path, [[
        (BODY, BODY_SIZE, [
            "This cover page paragraph appears before any styled heading",
            "in the document and must not be discarded from the retrievable corpus.",
        ]),
        (BODY, HEADING_SIZE, ["Key Benefits"]),
        (BODY, BODY_SIZE, [
            "Guaranteed maturity benefit payable at the end of the policy term.",
        ]),
    ]])

    parsed = parse_brochure(str(pdf_path))

    preamble_sections = [s for s in parsed.sections if s.heading_path == []]
    assert preamble_sections, "pre-heading paragraph was dropped from sections"
    assert "cover page paragraph" in preamble_sections[0].text

    named_sections = [s for s in parsed.sections if s.heading_path == ["Key Benefits"]]
    assert named_sections
    assert "maturity benefit" in named_sections[0].text


def test_uin_and_descriptor_extracted_as_metadata(tmp_path):
    pdf_path = tmp_path / "brochure.pdf"
    _pdf(pdf_path, [[
        (BODY, HEADING_SIZE, ["Product Overview"]),
        (BODY, BODY_SIZE, [
            "UIN: 116N178V05",
            "An Individual Non-Linked Non-Participating Life Insurance Plan.",
        ]),
    ]])

    parsed = parse_brochure(str(pdf_path))

    assert parsed.uin == "116N178V05"
    assert parsed.descriptor is not None
    assert "Participating" in parsed.descriptor


def test_running_footer_suppressed_from_sections(tmp_path):
    """Short text repeated on every page (a footer/running header) must not
    itself become section body text."""
    pdf_path = tmp_path / "brochure.pdf"
    footer = "Acme Life Confidential"
    _pdf(pdf_path, [
        [
            (BODY, HEADING_SIZE, ["Section One"]),
            (BODY, BODY_SIZE, ["Section one explains the benefit in detail here."]),
            (BODY, BODY_SIZE, [footer]),
        ],
        [
            (BODY, HEADING_SIZE, ["Section Two"]),
            (BODY, BODY_SIZE, ["Section two explains the charges in detail here."]),
            (BODY, BODY_SIZE, [footer]),
        ],
    ])

    parsed = parse_brochure(str(pdf_path))

    all_text = " ".join(s.text for s in parsed.sections)
    assert footer not in all_text
    assert "Section one explains" in all_text
    assert "Section two explains" in all_text
