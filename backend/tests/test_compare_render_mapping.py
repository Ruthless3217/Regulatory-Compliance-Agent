"""Render overlay mapping — synthetic words/marks, no real PDF."""
from app.services.pdf_render_service import PositionedWord, PageMeta
from app.services.render_orchestrator import _build_pages, _build_changes


def _meta(n, w=600.0, h=800.0):
    return PageMeta(n=n, w_pt=w, h_pt=h, image_path=f"page-{n:04d}.png")


def _word(text, page, x0, y0, x1, y1):
    return PositionedWord(text=text, page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def test_build_pages_unions_same_line_same_change():
    words = [
        _word("alpha", 1, 10, 100, 40, 112),
        _word("beta", 1, 44, 100, 80, 112),   # same line, same change -> one box
        _word("gamma", 2, 10, 200, 60, 212),  # page 2, different change
    ]
    marks = [
        {"index": 0, "type": "removed", "change_id": "r0"},
        {"index": 1, "type": "removed", "change_id": "r0"},
        {"index": 2, "type": "removed", "change_id": "r1"},
    ]
    pages = _build_pages([_meta(1), _meta(2)], words, marks, "removed")
    p1 = next(p for p in pages if p["n"] == 1)
    p2 = next(p for p in pages if p["n"] == 2)
    assert len(p1["boxes"]) == 1
    assert p1["boxes"][0] == {
        "box_id": "r0-p1-b0",
        "x0": 10,
        "y0": 100,
        "x1": 80,
        "y1": 112,
        "type": "removed",
        "change_id": "r0",
    }
    assert p1["w_pt"] == 600.0 and p1["h_pt"] == 800.0
    assert len(p2["boxes"]) == 1
    assert p2["boxes"][0]["change_id"] == "r1"
    assert p2["boxes"][0]["box_id"] == "r1-p2-b0"


def test_build_pages_separate_lines_get_separate_boxes():
    words = [
        _word("line1", 1, 10, 100, 60, 112),
        _word("line2", 1, 10, 130, 60, 142),  # different top -> different line
    ]
    marks = [
        {"index": 0, "type": "added", "change_id": "r0"},
        {"index": 1, "type": "added", "change_id": "r0"},
    ]
    pages = _build_pages([_meta(1)], words, marks, "added")
    assert len(pages[0]["boxes"]) == 2
    assert all(b["type"] == "added" for b in pages[0]["boxes"])
    assert pages[0]["boxes"][0]["box_id"] == "r0-p1-b0"
    assert pages[0]["boxes"][1]["box_id"] == "r0-p1-b1"


def test_build_pages_distant_words_on_same_line_get_separate_boxes():
    words = [
        _word("alpha", 1, 10, 100, 40, 112),
        # Gap of 60pt (representing unchanged text between them)
        _word("omega", 1, 100, 100, 140, 112),
    ]
    marks = [
        {"index": 0, "type": "modified", "change_id": "r0"},
        {"index": 1, "type": "modified", "change_id": "r0"},
    ]
    pages = _build_pages([_meta(1)], words, marks, "removed")
    assert len(pages[0]["boxes"]) == 2
    assert pages[0]["boxes"][0]["box_id"] == "r0-p1-b0"
    assert pages[0]["boxes"][0]["x0"] == 10 and pages[0]["boxes"][0]["x1"] == 40
    assert pages[0]["boxes"][1]["box_id"] == "r0-p1-b1"
    assert pages[0]["boxes"][1]["x0"] == 100 and pages[0]["boxes"][1]["x1"] == 140


def test_build_changes_attaches_old_and_new_refs_with_locations():
    old_words = [
        _word("gone", 1, 10, 100, 50, 112),
        _word("part2", 2, 10, 100, 50, 112),
    ]
    new_words = [_word("fresh", 1, 10, 100, 55, 112)]
    old_marks = [
        {"index": 0, "type": "removed", "change_id": "r0"},
        {"index": 1, "type": "removed", "change_id": "r0"},  # multi-page change
        {"index": 0, "type": "changed", "change_id": "r1"},
    ]
    new_marks = [
        {"index": 0, "type": "added", "change_id": "r2"},
        {"index": 0, "type": "changed", "change_id": "r1"},
    ]
    changes = [
        {"id": "r0", "kind": "removed", "old_text": "gone part2", "new_text": ""},
        {"id": "r2", "kind": "added", "old_text": "", "new_text": "fresh"},
        {"id": "r1", "kind": "modified", "old_text": "gone", "new_text": "fresh"},
    ]
    out = _build_changes(changes, old_words, old_marks, new_words, new_marks)
    by_id = {c["id"]: c for c in out}
    assert set(by_id["r0"].keys()) == {"id", "kind", "old"}
    assert by_id["r0"]["old"]["page"] == 1
    assert by_id["r0"]["old"]["bbox"] == [10, 100, 50, 112]
    assert by_id["r0"]["old"]["text"] == "gone part2"
    assert len(by_id["r0"]["old"]["locations"]) == 2
    assert by_id["r0"]["old"]["locations"][0]["page"] == 1
    assert by_id["r0"]["old"]["locations"][1]["page"] == 2

    assert set(by_id["r2"].keys()) == {"id", "kind", "new"}
    assert by_id["r2"]["new"]["text"] == "fresh"
    assert len(by_id["r2"]["new"]["locations"]) == 1

    assert "old" in by_id["r1"] and "new" in by_id["r1"]  # modified carries both
    assert len(by_id["r1"]["old"]["locations"]) == 1
    assert len(by_id["r1"]["new"]["locations"]) == 1

