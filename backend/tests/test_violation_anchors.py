"""violation_anchor_service — mapping a finding's quoted text to a page/bbox.

Real reportlab PDFs through the real extractor (same approach as
test_submission_render_service.py), plus MagicMock DB objects for the write
pass so no live database is needed.
"""
import os
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

from app.services import violation_anchor_service as vas
from app.services import submission_render_service as srs
from app.services.pdf_render_service import PositionedWord

reportlab = pytest.importorskip("reportlab")
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402

PAGE_W, PAGE_H = letter


def _make_pdf(path, lines):
    c = canvas.Canvas(path, pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 24
    c.showPage()
    c.save()


def _words(pdf_path):
    return srs.submission_positioned_words(str(pdf_path))


def _violation(current_text):
    return SimpleNamespace(current_text=current_text, anchor_page=None, anchor_bbox=None)


def _db_with(violations):
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.all.return_value = violations
    return db


# --- the pure matcher -------------------------------------------------------

def test_phrase_found_once_gets_page_and_plausible_bbox(tmp_path):
    pdf = tmp_path / "one.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life.", "Terms and conditions apply."])

    hit = vas.locate(_words(pdf), "Guaranteed returns")

    assert hit is not None
    page, (x0, y0, x1, y1) = hit
    assert page == 1
    # Drawn at x=72 from the left, near the top of a 612x792 page.
    assert 70 <= x0 <= 75
    assert x1 > x0 and y1 > y0
    assert x1 <= PAGE_W and y1 <= PAGE_H
    assert y0 < PAGE_H / 2


def test_phrase_appearing_twice_stays_null(tmp_path):
    pdf = tmp_path / "twice.pdf"
    _make_pdf(str(pdf), [
        "Guaranteed returns for life.",
        "Some other copy in between.",
        "Guaranteed returns for life.",
    ])

    assert vas.locate(_words(pdf), "Guaranteed returns") is None


def test_absent_phrase_stays_null(tmp_path):
    pdf = tmp_path / "absent.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])

    assert vas.locate(_words(pdf), "tax free withdrawals") is None


def test_whitespace_and_case_differences_still_match(tmp_path):
    pdf = tmp_path / "norm.pdf"
    _make_pdf(str(pdf), ["Please read the policy document carefully."])

    hit = vas.locate(_words(pdf), "  READ   the\n Policy  Document ")

    assert hit is not None
    assert hit[0] == 1


def test_empty_text_is_a_miss(tmp_path):
    pdf = tmp_path / "empty.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])

    assert vas.locate(_words(pdf), "") is None
    assert vas.locate(_words(pdf), "   ") is None


def test_match_must_land_on_whole_words(tmp_path):
    pdf = tmp_path / "partial.pdf"
    _make_pdf(str(pdf), ["Unguaranteed returns are possible."])

    # "guaranteed returns" is a substring of "Unguaranteed returns" but is not
    # the same claim — boxing it would flag text the finding never quoted.
    assert vas.locate(_words(pdf), "guaranteed returns") is None


def test_phrase_spanning_a_line_break_boxes_only_the_first_line():
    # Two visual lines: "the guaranteed" / "returns shown". A box unioning all
    # four words would swallow the whole block; only the first line is boxed.
    words = [
        PositionedWord("the", 1, 72.0, 100.0, 90.0, 112.0),
        PositionedWord("guaranteed", 1, 94.0, 100.0, 160.0, 112.0),
        PositionedWord("returns", 1, 72.0, 118.0, 120.0, 130.0),
        PositionedWord("shown", 1, 124.0, 118.0, 170.0, 130.0),
    ]

    hit = vas.locate(words, "the guaranteed returns shown")

    assert hit == (1, [72.0, 100.0, 160.0, 112.0])


def test_second_page_reports_one_based_page():
    words = [
        PositionedWord("alpha", 2, 10.0, 20.0, 40.0, 32.0),
        PositionedWord("beta", 2, 44.0, 20.0, 70.0, 32.0),
    ]

    assert vas.locate(words, "alpha beta") == (2, [10.0, 20.0, 70.0, 32.0])


def test_duplicate_across_pages_is_also_ambiguous():
    words = [
        PositionedWord("alpha", 1, 10.0, 20.0, 40.0, 32.0),
        PositionedWord("alpha", 2, 10.0, 20.0, 40.0, 32.0),
    ]

    assert vas.locate(words, "alpha") is None


# --- anchoring after analysis ------------------------------------------------
#
# The render job anchors at UPLOAD time, when a submission has no findings yet.
# `anchor_now` is the pass the analyzer runs once they exist — without it every
# violation keeps a NULL anchor and the reviewer's page view draws no boxes.


def _analysed(content_type="pdf", file_path=None):
    return SimpleNamespace(id="sub-1", content_type=content_type, file_path=file_path)


def test_anchor_now_measures_a_pdf_against_its_own_upload(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])
    found = _violation("Guaranteed returns")

    anchored = srs.anchor_now(_db_with([found]), _analysed(file_path=str(pdf)))

    assert anchored == 1 and found.anchor_page == 1


def test_anchor_now_is_a_no_op_without_a_rendered_pdf():
    """A pasted-text submission, or a render that has not finished yet."""
    v = _violation("Guaranteed returns")

    assert srs.anchor_now(_db_with([v]), _analysed(content_type="txt")) == 0
    assert srs.anchor_now(_db_with([v]), _analysed(file_path=None)) == 0
    assert v.anchor_page is None


def test_anchor_now_reads_the_converted_copy_for_a_docx(tmp_path, monkeypatch):
    """A DOCX has no geometry of its own — `_render` leaves the Gotenberg
    conversion at renders_dir/source.pdf, and that is what is measured."""
    base = tmp_path / "renders"
    base.mkdir()
    _make_pdf(str(base / "source.pdf"), ["Guaranteed returns for life."])
    monkeypatch.setattr(srs, "renders_dir", lambda _sid: str(base))
    found = _violation("Guaranteed returns")

    anchored = srs.anchor_now(
        _db_with([found]), _analysed(content_type="docx", file_path="upload.docx")
    )

    assert anchored == 1 and found.anchor_page == 1


# --- the write pass ---------------------------------------------------------

def test_pass_writes_anchors_for_found_text_only(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life.", "Terms and conditions apply."])

    found = _violation("Guaranteed returns")
    missing = _violation("tax free withdrawals")
    blank = _violation(None)
    db = _db_with([found, missing, blank])

    anchored = vas.anchor_submission_violations(db, "sub-1", str(pdf))

    assert anchored == 1
    assert found.anchor_page == 1 and len(found.anchor_bbox) == 4
    assert missing.anchor_page is None and missing.anchor_bbox is None
    assert blank.anchor_page is None and blank.anchor_bbox is None


def test_pass_never_raises_on_a_corrupt_file(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a pdf at all")

    assert vas.anchor_submission_violations(_db_with([_violation("anything")]), "sub-1", str(bad)) == 0


def test_pass_never_raises_on_a_missing_file(tmp_path):
    missing = str(tmp_path / "nope.pdf")

    assert vas.anchor_submission_violations(_db_with([_violation("anything")]), "sub-1", missing) == 0


def test_pass_never_raises_on_a_broken_db(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])
    db = MagicMock()
    db.query.side_effect = RuntimeError("connection went away")

    assert vas.anchor_submission_violations(db, "sub-1", str(pdf)) == 0


# --- wiring into the render job --------------------------------------------

def _submission(content_type="pdf", file_path="does-not-matter.pdf"):
    sub = MagicMock()
    sub.id = "sub-1"
    sub.content_type = content_type
    sub.file_path = file_path
    return sub


def test_run_render_anchors_after_a_successful_render(monkeypatch, tmp_path):
    monkeypatch.setattr(srs.settings, "upload_dir", str(tmp_path / "up"))
    pdf = tmp_path / "in.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])

    sub = _submission(file_path=str(pdf))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = sub
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    calls = []
    monkeypatch.setattr(
        srs, "anchor_submission_violations",
        lambda d, sid, path: calls.append((sid, path)) or 0,
    )

    srs.run_render("sub-1")

    assert sub.page_render_status == "completed"
    assert len(calls) == 1
    assert calls[0][0] == "sub-1"
    assert os.path.exists(calls[0][1])


def test_run_render_does_not_anchor_a_non_pdf_submission(monkeypatch, tmp_path):
    sub = _submission(content_type="html", file_path=str(tmp_path / "x.html"))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = sub
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    def _boom(*a, **kw):
        raise AssertionError("anchor pass ran for a submission with no page geometry")

    monkeypatch.setattr(srs, "anchor_submission_violations", _boom)

    srs.run_render("sub-1")

    assert sub.page_render_status == "skipped"


def test_run_render_does_not_anchor_a_failed_render(monkeypatch, tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(str(pdf), ["Guaranteed returns for life."])
    sub = _submission(file_path=str(pdf))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = sub
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    def _explode(*a, **kw):
        raise RuntimeError("rasterizer exploded")

    monkeypatch.setattr(srs, "_render", _explode)

    def _boom(*a, **kw):
        raise AssertionError("anchor pass ran after a failed render")

    monkeypatch.setattr(srs, "anchor_submission_violations", _boom)

    srs.run_render("sub-1")

    assert sub.page_render_status == "failed"
