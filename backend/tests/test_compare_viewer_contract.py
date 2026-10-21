"""Phase 4 / Stage F1 — backend contract for the Draftable-style viewer.

* every change record is kept (hidden ones too) with category / subtype / filter_group /
  default_visible; typographic-only changes are hidden by default, currency stays visible;
* one-sided changes carry an anchor on the empty side so navigation reaches both panes;
* render_result carries boxes for all records and a page alignment map;
* text blocks carry change ids so the text view filters with the same records;
* exports draw only the enabled records, align pages with blank fillers, and a
  single-document export exists.
"""
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services.pdf_render_service import PositionedWord, PageMeta
from app.services.content_diff_service import compare_words, to_blocks, page_alignment
from app.services import render_orchestrator as ro
from app.config import settings


def words(text, page=1, y=100.0, line=0, block=0):
    out, x = [], 40.0
    for t in text.split():
        out.append(PositionedWord(text=t, page=page, x0=x, y0=y, x1=x + 5 * len(t), y1=y + 10, line_id=line, block_id=block))
        x += 5 * len(t) + 3
    return out


def by_text(diff, old=None, new=None):
    for c in diff.changes:
        if (old is None or c.old_text == old) and (new is None or c.new_text == new):
            return c
    raise AssertionError((old, new, [(c.old_text, c.new_text) for c in diff.changes]))


# ---------------------------------------------------------------- visibility model
@pytest.mark.parametrize("a,b,group,visible", [
    # Draftable parity: every group is shown by default; groups only switch categories off
    ("the Policy term", "the policy term", "case", True),
    ("Maharaja's College", "Maharaja’s College", "quotes", True),
    ("PART - A letter", "PART A letter", "punctuation", True),
    ("UIN: 116B063V01) x", "UIN: 116B0 63V01) x", "spacing", True),
    ("Sum Assured (Rs.) payable", "Sum Assured (₹) payable", "currency", True),
    ("within 30 days", "within 15 days", "replacements", True),
    ("Date <dd/mm/yyyy> here", "Date ______ here", "placeholders", True),
])
def test_filter_group_and_default_visibility(a, b, group, visible):
    d = compare_words(words(a), words(b))
    groups = {(c.filter_group, c.default_visible) for c in d.changes}
    assert (group, visible) in groups


def test_insertion_deletion_move_groups_are_visible():
    d = compare_words(words("alpha beta gamma delta epsilon"), words("alpha gamma delta epsilon zeta"))
    assert by_text(d, old="beta").filter_group == "deletions" and by_text(d, old="beta").default_visible
    assert by_text(d, new="zeta").filter_group == "insertions" and by_text(d, new="zeta").default_visible


# ---------------------------------------------------------------- one-sided anchors
def test_insertion_has_an_anchor_on_the_old_side_and_deletion_on_the_new_side():
    old = words("If You are not satisfied You may approach")
    new = words("If You are not satisfied within 30 days You may approach")
    d = compare_words(old, new)
    ins = by_text(d, new="within 30 days")
    assert ins.old_words == [] and ins.old_anchor == (4, "after")      # after 'satisfied'
    d2 = compare_words(new, old)
    dele = by_text(d2, old="within 30 days")
    assert dele.new_words == [] and dele.new_anchor == (4, "after")


def test_insertion_at_document_start_anchors_before_first_word():
    d = compare_words(words("alpha beta"), words("zero alpha beta"))
    assert by_text(d, new="zero").old_anchor == (0, "before")


# ---------------------------------------------------------------- render_result contract
@pytest.fixture
def fake_render(monkeypatch, tmp_path):
    monkeypatch.setattr(ro.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "compare_engine", "content")
    old = words("Grievance Cell Centre the Policy term", page=1) + words("closing words here", page=2)
    new = words("Grievance Call Centre within 30 days the policy term", page=1)
    monkeypatch.setattr(ro, "to_pdf", lambda p, ct, d, side: f"{side}.pdf")
    monkeypatch.setattr(ro, "render_pages", lambda pdf, d, cap: (
        [PageMeta(1, 600.0, 800.0, "p1.png"), PageMeta(2, 600.0, 800.0, "p2.png")] if pdf == "old.pdf"
        else [PageMeta(1, 600.0, 800.0, "p1.png")], 0))
    monkeypatch.setattr(ro, "positioned_words_split", lambda pdf: (old, []) if pdf == "old.pdf" else (new, []))
    return old, new


def test_render_result_keeps_every_record_with_boxes(fake_render):
    rr = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    assert {"old", "new", "changes", "truncated_pages"} <= set(rr)
    case = next(c for c in rr["changes"] if c.get("filter_group") == "case")
    assert case["default_visible"] is True and case["category"] == "typographic"
    boxes = [b for p in rr["new"]["pages"] for b in p["boxes"] if b["change_id"] == case["id"]]
    assert boxes, "every record keeps its coordinates"
    content = next(c for c in rr["changes"] if c.get("filter_group") == "replacements")
    assert content["default_visible"] is True


def test_render_result_one_sided_change_has_anchor_ref(fake_render):
    rr = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    ins = next(c for c in rr["changes"] if c["kind"] == "added")
    assert ins["new"]["text"] == "within 30 days"
    assert ins["old"]["anchor"] is True and ins["old"]["text"] == ""
    x0, y0, x1, y1 = ins["old"]["bbox"]
    assert x1 - x0 <= 2 and y1 > y0


def test_render_result_has_page_alignment(fake_render):
    rr = ro._render_pair("c1", "o.docx", "n.pdf", "docx", "pdf")
    assert rr["page_map"] == [[1, 1], [2, None]]


def test_page_alignment_inserts_blank_fillers_both_ways():
    old = words("a1 a2 a3", page=1) + words("b1 b2 b3", page=2) + words("c1 c2 c3", page=3)
    new = words("a1 a2 a3", page=1) + words("x1 x2 x3", page=2) + words("b1 b2 b3 c1 c2 c3", page=3)
    assert page_alignment(compare_words(old, new), 3, 3) == [[1, 1], [None, 2], [2, 3], [3, None]]


# ---------------------------------------------------------------- text blocks carry record ids
def test_text_blocks_words_carry_change_ids_and_groups():
    d = compare_words(words("Grievance Cell Centre the Policy term"), words("Grievance Call Centre the policy term"))
    blocks = to_blocks(d)
    rep = [b for b in blocks if b["type"] == "replace"]
    cell = next(w for b in rep for w in b["old_words"] if w["text"] == "Cell")
    assert cell["changed"] is True and cell["change_id"] and cell["filter_group"] == "replacements"
    policy = next(w for b in blocks for w in (b.get("old_words") or []) if w["text"] == "Policy")
    assert policy["changed"] is True and policy["filter_group"] == "case" and policy["change_id"]


# ---------------------------------------------------------------- exports
from app.services import export_service  # noqa: E402


def _comparison(tmp_path, monkeypatch, rr):
    monkeypatch.setattr(export_service, "renders_dir", lambda cid: str(tmp_path))
    for side, n in (("old", 1), ("old", 2), ("new", 1)):
        (tmp_path / side).mkdir(exist_ok=True)
        Image.new("RGB", (600, 800), "white").save(tmp_path / side / f"page-{n:04d}.png")
    return SimpleNamespace(id="c1", title="t", render_status="completed", render_result=rr, diff_result=[],
                           annotations=[], status="completed")


def _rr():
    return {
        "old": {"pages": [{"n": 1, "w_pt": 600, "h_pt": 800, "boxes": [
                    {"x0": 100, "y0": 100, "x1": 200, "y1": 120, "type": "removed", "change_id": "vis"},
                    {"x0": 100, "y0": 300, "x1": 200, "y1": 320, "type": "removed", "change_id": "hid"}]},
                          {"n": 2, "w_pt": 600, "h_pt": 800, "boxes": []}]},
        "new": {"pages": [{"n": 1, "w_pt": 600, "h_pt": 800, "boxes": [
                    {"x0": 100, "y0": 100, "x1": 200, "y1": 120, "type": "added", "change_id": "vis"}]}]},
        "changes": [
            {"id": "vis", "kind": "modified", "default_visible": True, "filter_group": "replacements",
             "old": {"page": 1, "bbox": [100, 100, 200, 120], "text": "Cell"},
             "new": {"page": 1, "bbox": [100, 100, 200, 120], "text": "Call"}},
            {"id": "hid", "kind": "modified", "default_visible": False, "filter_group": "case",
             "old": {"page": 1, "bbox": [100, 300, 200, 320], "text": "Policy"},
             "new": {"page": 1, "bbox": [100, 300, 200, 320], "text": "policy"}},
            {"id": "del", "kind": "removed", "default_visible": True, "filter_group": "deletions",
             "old": {"page": 2, "bbox": [50, 50, 60, 60], "text": "gone"},
             "new": {"page": 1, "bbox": [300, 500, 301, 510], "text": "", "anchor": True}},
        ],
        "truncated_pages": 0,
        "page_map": [[1, 1], [2, None]],
    }


def _pages(pdf_bytes):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    return [doc[i].render(scale=1).to_pil().convert("RGB") for i in range(len(doc))]


def _px(img, x_pt, y_pt, w_pt=600, h_pt=800, x_off=0):
    sx, sy = (img.width - x_off) / w_pt if not x_off else 1, img.height / h_pt
    return img.getpixel((int(x_off + x_pt * sx), int(y_pt * sy)))


def test_highlighted_pdf_draws_only_enabled_records(tmp_path, monkeypatch):
    comp = _comparison(tmp_path, monkeypatch, _rr())
    pages = _pages(export_service.highlighted_pdf(comp, "old"))
    p1 = pages[0]
    sx = p1.width / 600
    assert p1.getpixel((int(150 * sx), int(110 * sx))) != (255, 255, 255)   # visible record drawn
    assert p1.getpixel((int(150 * sx), int(310 * sx))) == (255, 255, 255)   # hidden record not drawn
    pages = _pages(export_service.highlighted_pdf(comp, "old", groups={"replacements", "case"}))
    assert pages[0].getpixel((int(150 * sx), int(310 * sx))) != (255, 255, 255)


def test_side_by_side_pdf_is_aligned_and_highlighted(tmp_path, monkeypatch):
    comp = _comparison(tmp_path, monkeypatch, _rr())
    sheets = _pages(export_service.side_by_side_pdf(comp))
    assert len(sheets) == 2                                   # page_map has 2 sheets
    s1, s2 = sheets
    half = s1.width / 2
    scale = (half - 12) / 600
    assert s1.getpixel((int(150 * scale), int(110 * scale))) != (255, 255, 255)          # old highlight
    assert s1.getpixel((int(half + 12 + 150 * scale), int(110 * scale))) != (255, 255, 255)  # new highlight
    # sheet 2: old page 2 on the left, blank filler on the right
    assert all(s2.getpixel((int(half + 12 + x * scale), int(y * scale))) == (255, 255, 255)
               for x, y in ((100, 100), (300, 400), (500, 700)))


def test_single_document_pdf_marks_insertions_and_deletion_anchor(tmp_path, monkeypatch):
    comp = _comparison(tmp_path, monkeypatch, _rr())
    pages = _pages(export_service.single_document_pdf(comp))
    assert len(pages) == 1
    p = pages[0]
    sx = p.width / 600
    assert p.getpixel((int(150 * sx), int(110 * sx))) != (255, 255, 255)   # change on new side
    assert p.getpixel((int(300.5 * sx), int(505 * sx))) != (255, 255, 255)  # deletion caret at anchor


def test_derive_changes_skips_hidden_unless_requested():
    rr = _rr()
    c = SimpleNamespace(render_status="completed", render_result=rr, diff_result=[])
    assert [x["change_id"] for x in export_service.derive_changes(c)] == ["vis", "del"]
    assert [x["change_id"] for x in export_service.derive_changes(c, groups={"case", "replacements", "deletions"})] == ["vis", "hid", "del"]


def test_export_route_knows_single_document_and_groups():
    from app.api.routes import comparisons as routes
    assert "single-document.pdf" in routes._EXPORT_MEDIA and "single-document.pdf" in routes._PDF_EXPORT_KINDS
    assert routes._parse_groups("case,quotes") == {"case", "quotes"}
    assert routes._parse_groups(None) is None


def test_text_blocks_of_every_type_expose_record_ids():
    old = words("Nomination may be cancelled. This sentence was removed entirely. Grace Policy applies.")
    new = words("Nomination may be cancelled. Grace policy applies. Brand new sentence here.")
    blocks = to_blocks(compare_words(old, new))
    dele = next(b for b in blocks if b["type"] == "delete")
    ins = next(b for b in blocks if b["type"] == "insert")
    assert dele["old_words"] and all(w["change_id"] for w in dele["old_words"])
    assert ins["new_words"] and all(w["change_id"] for w in ins["new_words"])
    # a case-only record inside an otherwise equal sentence is shown with its group
    row = next(b for b in blocks if any(w["text"] == "Grace" for w in (b.get("old_words") or [])))
    pol = next(w for w in row["old_words"] if w["text"] == "Policy")
    assert pol["filter_group"] == "case" and pol["changed"] is True


def test_moved_text_blocks_carry_their_change_id():
    a = "Fraud and Misrepresentation The Policy has been issued based on the details provided"
    b = "Modification Any modifications to the Policy will be carried out by endorsement"
    old = words(a, line=0, block=0) + words(b, y=200, line=1, block=1)
    new = words(b, line=0, block=0) + words(a, y=200, line=1, block=1)
    moved = [x for x in to_blocks(compare_words(old, new)) if x.get("moved")]
    assert moved and all(x.get("change_id") for x in moved)
