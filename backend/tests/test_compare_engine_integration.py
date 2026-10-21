"""Stage C integration — the content-first engine behind the existing entry points.

* ``settings.compare_engine`` is a real setting (default "content"); "structural" and
  "legacy" keep the previous engines selectable for side-by-side validation.
* ``build_diff`` (text mode at create time) runs the content engine on extracted text.
* ``run_render`` computes one change set and persists BOTH the overlay and the text
  redline from it, so the two views cannot disagree once the render completes.
"""
from unittest.mock import MagicMock

import pytest

from app.config import Settings, settings
from app.services import comparison_service as cs
from app.services import render_orchestrator as ro
from app.services.pdf_render_service import PositionedWord, PageMeta


def test_compare_engine_is_a_declared_setting_defaulting_to_content():
    assert "compare_engine" in Settings.model_fields
    assert Settings.model_fields["compare_engine"].default == "content"


def _changed(blocks, wording_only=True):
    """Changed words per row. Placeholder fills are shown (Draftable parity) but are not
    wording changes; ``wording_only`` leaves them out."""
    def on(w):
        return w["changed"] and not (wording_only and w.get("filter_group") == "placeholders")
    out = []
    for b in blocks:
        if b["type"] == "replace":
            o, n = [w["text"] for w in b["old_words"] if on(w)], [w["text"] for w in b["new_words"] if on(w)]
            if o or n:
                out.append((o, n))
        elif b["type"] in ("insert", "delete"):
            out.append(b["type"])
    return out


def test_build_diff_ignores_reflowed_segments(monkeypatch):
    monkeypatch.setattr(settings, "compare_engine", "content")
    old = ["Grace Period is thirty days.", "It applies to all premiums."]
    new = ["Grace Period is thirty", "days. It applies to all premiums."]
    assert _changed(cs.build_diff(old, new)) == []


def test_build_diff_localises_a_word_edit(monkeypatch):
    monkeypatch.setattr(settings, "compare_engine", "content")
    blocks = cs.build_diff(["The IRDAI Grievance Cell Centre handles complaints."],
                           ["The IRDAI Grievance Call Centre handles complaints."])
    assert _changed(blocks) == [(["Cell"], ["Call"])]


def test_build_diff_reports_placeholder_fills_as_placeholders(monkeypatch):
    monkeypatch.setattr(settings, "compare_engine", "content")
    blocks = cs.build_diff(["Date of Birth <dd/mm/yyyy> applies."], ["Date of Birth ______ applies."])
    assert _changed(blocks) == []                                 # not a wording change
    assert _changed(blocks, wording_only=False) == [(["<dd/mm/yyyy>"], ["______"])]   # but shown


@pytest.mark.parametrize("engine,target", [("structural", "structural"), ("legacy", "legacy")])
def test_previous_engines_remain_selectable(monkeypatch, engine, target):
    monkeypatch.setattr(settings, "compare_engine", engine)
    import app.services.structural_alignment_service as sas
    monkeypatch.setattr(sas, "structural_build_diff", lambda o, n: ["structural"])
    out = cs.build_diff(["a b."], ["a c."])
    assert (out == ["structural"]) == (target == "structural")


def _words(text, page=1):
    out, x = [], 40.0
    for i, t in enumerate(text.split()):
        out.append(PositionedWord(text=t, page=page, x0=x, y0=100, x1=x + 5 * len(t), y1=110, line_id=0, block_id=0))
        x += 5 * len(t) + 3
    return out


@pytest.fixture
def fake_render(monkeypatch, tmp_path):
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "compare_engine", "content")
    old = _words("approach the IRDAI Grievance Cell Centre on contact Date <dd/mm/yyyy> here")
    new = _words("approach the IRDAI Grievance Call Centre on contact Date ________ here")
    monkeypatch.setattr(ro, "to_pdf", lambda p, ct, d, side: f"{side}.pdf")
    monkeypatch.setattr(ro, "render_pages", lambda pdf, d, cap: ([PageMeta(1, 600.0, 800.0, "p.png")], 0))
    monkeypatch.setattr(ro, "positioned_words_split", lambda pdf: (old, []) if pdf == "old.pdf" else (new, []))
    return old, new


def test_render_pair_keeps_the_render_contract(fake_render):
    res = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    assert {"old", "new", "changes", "truncated_pages"} <= set(res)   # additive: page_map
    shown = [c for c in res["changes"] if c.get("filter_group") not in ("placeholders",)]
    assert [(c.get("old", {}).get("text"), c.get("new", {}).get("text")) for c in shown] == [("Cell", "Call")]
    assert any(c.get("filter_group") == "placeholders" and c["default_visible"] for c in res["changes"])


def test_run_render_persists_overlay_and_text_from_the_same_records(fake_render, monkeypatch):
    comp = MagicMock()
    comp.id = "c1"
    comp.old_content_type, comp.new_content_type = "docx", "pdf"
    comp.old_file_path, comp.new_file_path = "o.docx", "n.pdf"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = comp
    monkeypatch.setattr(ro, "SessionLocal", lambda: db)
    ro.run_render("c1")
    assert comp.render_status == "completed"
    assert _changed(comp.diff_result) == [(["Cell"], ["Call"])]
    overlay = [(c["old"]["text"], c["new"]["text"]) for c in comp.render_result["changes"]
               if c["filter_group"] != "placeholders"]
    assert overlay == [("Cell", "Call")]
