"""Draftable parity — what the viewer shows and how unchanged text is linked.

Observed on the reference session (phase5 probes):
* every difference Draftable detects is shown by default (case, punctuation, quotes,
  placeholders, renumbering, running headers/footers); only style changes are filtered;
* hovering any unchanged sentence/paragraph outlines it AND its counterpart on the other
  document; clicking selects it and aligns both documents on it. The unit is the run of
  matched text between two changes, split at sentence/paragraph boundaries;
* running headers/footers are compared page against page (they are not body text, so
  they never disturb the body alignment) and reported as their own filter group.
"""
import pytest

from app.config import settings
from app.services import render_orchestrator as ro
from app.services.content_diff_service import (
    ALL_GROUPS,
    DEFAULT_GROUPS,
    compare_furniture,
    compare_words,
    match_segments,
)
from app.services.pdf_render_service import PageMeta, PositionedWord


def words(text, page=1, y=100.0, x=40.0, line=0, block=0):
    out = []
    for t in text.split():
        out.append(PositionedWord(text=t, page=page, x0=x, y0=y, x1=x + 5 * len(t), y1=y + 10, line_id=line, block_id=block))
        x += 5 * len(t) + 3
    return out


def lines(*rows, page=1, y=100.0):
    """Several visual lines of one paragraph."""
    out = []
    for i, r in enumerate(rows):
        out += words(r, page=page, y=y + 14 * i, line=i)
    return out


def text_of(ws, idx):
    return " ".join(ws[i].text for i in idx)


# ---------------------------------------------------------------- default visibility
def test_every_group_is_visible_by_default():
    assert DEFAULT_GROUPS == ALL_GROUPS
    assert "headers_footers" in ALL_GROUPS


@pytest.mark.parametrize("a,b,group", [
    ("the Policy term", "the policy term", "case"),
    ("Maharaja's College", "Maharaja’s College", "quotes"),
    ("PART - A letter", "PART A letter", "punctuation"),
    ("Date <dd/mm/yyyy> here", "Date ______ here", "placeholders"),
])
def test_typographic_and_template_records_are_shown_like_draftable(a, b, group):
    d = compare_words(words(a), words(b))
    rec = next(c for c in d.changes if c.filter_group == group)
    assert rec.default_visible is True


# ---------------------------------------------------------------- matched-text units
def test_match_segments_split_at_sentence_ends_and_exclude_changes():
    old = words("You shall be provided a Free Look Period. In case You disagree return the Policy.")
    new = words("You shall be provided a Free Look Period. In case You disagree surrender the Policy.")
    segs = match_segments(compare_words(old, new))
    got = [(text_of(old, o), text_of(new, n)) for o, n in segs]
    assert ("You shall be provided a Free Look Period.", "You shall be provided a Free Look Period.") in got
    assert ("In case You disagree", "In case You disagree") in got
    assert ("the Policy.", "the Policy.") in got
    assert not any("return" in o or "surrender" in n for o, n in got)


def test_match_segments_split_at_page_and_paragraph_breaks():
    old = words("alpha beta gamma", page=1) + words("delta epsilon zeta", page=2)
    new = words("alpha beta gamma delta epsilon zeta", page=1)
    segs = match_segments(compare_words(old, new))
    assert [(text_of(old, o), text_of(new, n)) for o, n in segs] == [
        ("alpha beta gamma", "alpha beta gamma"), ("delta epsilon zeta", "delta epsilon zeta")]
    # a blank-line gap (new paragraph) on either side also ends a unit
    old = words("one two three", y=100) + words("four five six", y=150)
    new = words("one two three four five six", y=100)
    segs = match_segments(compare_words(old, new))
    assert len(segs) == 2


def test_a_short_last_line_ends_the_paragraph_unit():
    # "(4) ... stated in sub-section (3) above" ends without a full stop, on a short line;
    # the next paragraph starts below. Draftable treats them as two units.
    def para(rows, y0, first_line):
        out = []
        for i, r in enumerate(rows):
            out += words(r, y=y0 + 14 * i, line=first_line + i)
        return out
    a = ["processed and Regular Premium shall be refunded within seven days of receipt of such request as",
         "stated in sub-section three above"]
    b = ["For any queries kindly write to us at the below mentioned address and we assure and strive to",
         "provide you the best of services"]
    old = para(a, 100, 0) + para(b, 128, 2)
    new = para(a + b, 300, 0)          # same text, the second paragraph wraps identically
    got = [text_of(old, o) for o, _ in match_segments(compare_words(old, new))]
    assert len(got) == 2 and got[0].endswith("three above") and got[1].startswith("For any queries")
    # a wrapped (full-width) line does not end a unit
    assert "as stated" in got[0]


def test_typographic_record_splits_the_unit_it_sits_in():
    old = words("subject to the Policy terms and conditions")
    new = words("subject to the policy terms and conditions")
    segs = match_segments(compare_words(old, new))
    got = [text_of(old, o) for o, _ in segs]
    assert got == ["subject to the", "terms and conditions"]


# ---------------------------------------------------------------- headers / footers
def test_furniture_is_compared_page_against_page():
    old_f = words("Bajaj Life Insurance Limited eTouch II Page 1 of 22", page=1, y=800) + \
        words("Bajaj Life Insurance Limited eTouch II Page 2 of 22", page=2, y=800)
    new_f = words("Bajaj Life eTouch II", page=1, y=20) + words("Bajaj Life Insurance Limited Policy Document Ver. 1", page=1, y=800)
    marks_o, marks_n, changes, segs = compare_furniture(old_f, new_f, [[1, 1], [2, None]])
    assert changes and all(c["filter_group"] == "headers_footers" for c in changes)
    assert all(c["category"] == "layout" and c["subtype"] == "header-footer" for c in changes)
    assert all(c["id"].startswith("h") for c in changes)
    assert len({c["id"] for c in changes}) == len(changes)
    # page 2's footer has no counterpart page: deleted as a whole, marks point at its words
    page2 = {i for i, w in enumerate(old_f) if w.page == 2}
    assert page2 <= {m["index"] for m in marks_o}
    # unchanged furniture words are linked like body text
    assert any(text_of(old_f, o).startswith("Bajaj Life Insurance Limited") for o, n in segs)


@pytest.fixture
def fake_render(monkeypatch, tmp_path):
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "compare_engine", "content")
    old = lines("Grievance Cell Centre the Policy term.", "In case You are not satisfied.", page=1)
    new = lines("Grievance Call Centre the policy term.", "In case You are not satisfied.", page=1)
    old_f = words("eTouch II Ann H Page 1 of 1", page=1, y=780)
    new_f = words("Policy Document Ver. 1", page=1, y=780)
    monkeypatch.setattr(ro, "to_pdf", lambda p, ct, d, side: f"{side}.pdf")
    monkeypatch.setattr(ro, "render_pages", lambda pdf, d, cap: ([PageMeta(1, 600.0, 800.0, "p1.png")], 0))
    monkeypatch.setattr(ro, "positioned_words_split",
                        lambda pdf: (old, old_f) if pdf == "old.pdf" else (new, new_f))
    return old, new


def test_render_result_carries_matches_with_line_rects(fake_render):
    rr = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    ms = rr["matches"]
    assert ms and len({m["id"] for m in ms}) == len(ms)
    sat = next(m for m in ms if m["old"] and m["old"][0][2] >= 113)   # the second line
    page, x0, y0, x1, y1 = sat["old"][0]
    assert page == 1 and x1 > x0 and y1 > y0
    assert sat["new"] and sat["new"][0][0] == 1


def test_render_result_reports_headers_and_footers_in_order(fake_render):
    rr = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    hf = [c for c in rr["changes"] if c.get("filter_group") == "headers_footers"]
    assert hf and all(c["default_visible"] is True for c in hf)
    # boxes exist for the furniture records on both sides
    ids = {b["change_id"] for p in rr["old"]["pages"] for b in p["boxes"]}
    assert any(c["id"] in ids for c in hf)
    # a footer comes after the page's body changes in the list
    order = [c.get("filter_group") for c in rr["changes"]]
    assert order.index("headers_footers") > order.index("replacements")


# ---------------------------------------------------------------- progress (Draftable loader)
def test_render_reports_progress_through_the_four_stages(fake_render):
    seen = []
    ro._render_pair_with_text("c1", "o.docx", "n.pdf", "docx", "pdf",
                              on_progress=lambda stage, pct, msg: seen.append((stage, pct, msg)))
    stages = [s for s, _, _ in seen]
    order = ["convert", "extract", "compute", "compare"]
    assert [s for s in order if s in stages] == order
    assert stages == sorted(stages, key=order.index)                 # never goes backwards
    assert all(0 <= p <= 100 and m for _, p, m in seen)
    assert seen[-1][0] == "compare" and seen[-1][1] == 100


def test_api_exposes_progress_while_processing_only():
    from types import SimpleNamespace
    from app.api.routes.comparisons import _serialize
    base = dict(id="c1", title="t", old_content_type="docx", new_content_type="pdf", old_filename="a",
                new_filename="b", status="completed", error_message=None, render_error=None,
                created_at=None, diff_result=[])
    prog = {"stage": "compute", "percent": 40, "message": "Aligning words"}
    busy = _serialize(SimpleNamespace(**base, render_status="processing", render_result={"progress": prog}), include_diff=True)
    assert busy["render_progress"] == prog and busy["render_result"] is None
    done = _serialize(SimpleNamespace(**base, render_status="completed", render_result={"old": {}}), include_diff=True)
    assert done["render_progress"] is None and done["render_result"] == {"old": {}}
