"""submission_render_service — single-sided PDF page rendering.

Mirrors test_compare_render_e2e.py's approach (real reportlab-generated PDF
through the actual rasterizer) plus MagicMock-based tests for the DB
status-transition branches, matching test_run_tracker_staleness.py's style
(no live DB needed).
"""
import os

import pytest
from unittest.mock import MagicMock

from app.services import submission_render_service as srs

reportlab = pytest.importorskip("reportlab")
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402


def _make_pdf(path, lines):
    c = canvas.Canvas(path, pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 24
    c.showPage()
    c.save()


def _submission(content_type="pdf", file_path="does-not-matter.pdf"):
    sub = MagicMock()
    sub.id = "sub-1"
    sub.content_type = content_type
    sub.file_path = file_path
    return sub


def _db_returning(submission):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = submission
    return db


def test_renders_dir_is_scoped_per_submission(monkeypatch, tmp_path):
    monkeypatch.setattr(srs.settings, "upload_dir", str(tmp_path))
    d1 = srs.renders_dir("aaa")
    d2 = srs.renders_dir("bbb")
    assert d1 != d2
    assert d1.endswith(os.path.join("renders", "submissions", "aaa"))


def test_run_render_skips_non_pdf(monkeypatch):
    sub = _submission(content_type="docx")
    db = _db_returning(sub)
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    srs.run_render("sub-1")

    assert sub.page_render_status == "skipped"
    db.commit.assert_called_once()


def test_run_render_skips_missing_file(monkeypatch, tmp_path):
    sub = _submission(content_type="pdf", file_path=str(tmp_path / "missing.pdf"))
    db = _db_returning(sub)
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    srs.run_render("sub-1")

    assert sub.page_render_status == "skipped"


def test_run_render_missing_submission_is_a_noop(monkeypatch):
    db = _db_returning(None)
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    srs.run_render("sub-1")  # must not raise

    db.commit.assert_not_called()


def test_run_render_marks_failed_on_rasterizer_exception(monkeypatch, tmp_path):
    pdf_path = tmp_path / "in.pdf"
    _make_pdf(str(pdf_path), ["Some marketing copy."])
    sub = _submission(content_type="pdf", file_path=str(pdf_path))
    db = _db_returning(sub)
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    def _boom(*a, **kw):
        raise RuntimeError("rasterizer exploded")

    monkeypatch.setattr(srs, "_render", _boom)

    srs.run_render("sub-1")

    assert sub.page_render_status == "failed"


def test_run_render_completed_real_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(srs.settings, "upload_dir", str(tmp_path / "up"))

    pdf_path = tmp_path / "in.pdf"
    _make_pdf(str(pdf_path), [
        "Guaranteed returns are not guaranteed.",
        "Please read the policy document carefully.",
    ])
    sub = _submission(content_type="pdf", file_path=str(pdf_path))
    db = _db_returning(sub)
    monkeypatch.setattr(srs, "SessionLocal", lambda: db)

    srs.run_render("sub-1")

    assert sub.page_render_status == "completed"
    assert os.path.exists(
        str(tmp_path / "up" / "renders" / "submissions" / "sub-1" / "page-0001.png")
    )


def test_submission_positioned_words_reuses_pdf_render_service(tmp_path):
    pdf_path = tmp_path / "in.pdf"
    _make_pdf(str(pdf_path), ["Alpha beta gamma."])

    words = srs.submission_positioned_words(str(pdf_path))

    assert any(w.text == "Alpha" for w in words)
    assert all(w.page == 1 for w in words)
