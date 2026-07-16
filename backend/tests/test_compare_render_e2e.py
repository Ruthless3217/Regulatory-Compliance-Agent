"""End-to-end render on real generated PDFs (reportlab -> render_orchestrator).

Exercises the full pixel pipeline: render_pages (pypdfium2) + positioned_words
(pdfplumber) + word_level_ops mapping -> RenderResult, then a highlighted-PDF
export. Skips cleanly if reportlab is unavailable.
"""
import os
import io

import pytest

reportlab = pytest.importorskip("reportlab")
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402

from app.services.render_orchestrator import _render_pair  # noqa: E402
from app.services import export_service  # noqa: E402


def _make_pdf(path, lines):
    c = canvas.Canvas(path, pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 24
    c.showPage()
    c.save()


@pytest.fixture()
def pdf_pair(tmp_path):
    old_p = str(tmp_path / "old.pdf")
    new_p = str(tmp_path / "new.pdf")
    _make_pdf(old_p, [
        "The premium is payable annually for twenty years.",
        "Surrender is permitted after five completed policy years.",
        "This clause is deleted in the revised version entirely.",
    ])
    _make_pdf(new_p, [
        "The premium is payable monthly for twenty years.",
        "Surrender is permitted after five completed policy years.",
        "A brand new clause appears only in the revised file.",
    ])
    return old_p, new_p


def test_render_pair_shape_and_boxes(pdf_pair, tmp_path, monkeypatch):
    # Point the orchestrator's render dir at tmp by faking settings.upload_dir.
    from app.services import render_orchestrator as ro
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path / "up"))

    old_p, new_p = pdf_pair
    result = _render_pair("cmp-e2e", old_p, new_p)

    # Top-level shape matches RenderResult.
    assert set(result.keys()) == {"old", "new", "changes", "truncated_pages"}
    assert result["truncated_pages"] == 0
    for side in ("old", "new"):
        pages = result[side]["pages"]
        assert len(pages) == 1
        page = pages[0]
        assert page["n"] == 1 and page["w_pt"] > 0 and page["h_pt"] > 0
        for b in page["boxes"]:
            assert b["type"] == ("removed" if side == "old" else "added")
            assert b["x0"] <= b["x1"] and b["y0"] <= b["y1"]
            assert b["change_id"]

    # "annually" -> "monthly" is a real change; boxes exist on both sides.
    assert any(p["boxes"] for p in result["old"]["pages"])
    assert any(p["boxes"] for p in result["new"]["pages"])

    # Every change references at least one side.
    for c in result["changes"]:
        assert c["kind"] in {"removed", "added", "modified", "moved"}
        assert ("old" in c) or ("new" in c)

    # The rendered PNGs exist on disk where the page endpoint will look for them.
    assert os.path.exists(str(tmp_path / "up" / "renders" / "cmp-e2e" / "old" / "page-0001.png"))


def test_highlighted_pdf_from_real_render(pdf_pair, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.services import render_orchestrator as ro
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path / "up"))
    # export_service reads settings.upload_dir via ro.renders_dir -> same monkeypatched value.

    old_p, new_p = pdf_pair
    result = _render_pair("cmp-hl", old_p, new_p)
    comparison = SimpleNamespace(id="cmp-hl", render_status="completed", render_result=result)

    pdf_bytes = export_service.highlighted_pdf(comparison, "new")
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 1000
