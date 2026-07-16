"""Positioned search matcher (pure — no PDF I/O)."""
from app.services.comparison_service import match_query_in_words


def _w(text, x0, top, x1, bottom):
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


PAGE = [
    _w("The", 10, 100, 30, 112),
    _w("premium", 34, 100, 90, 112),
    _w("is", 94, 100, 104, 112),
    _w("payable", 108, 100, 160, 112),
    _w("annually", 164, 100, 220, 112),
]


def test_single_word_hit_bbox_is_that_word():
    hits = match_query_in_words(PAGE, "premium", page=1, cap=200)
    assert len(hits) == 1
    assert hits[0]["page"] == 1
    assert hits[0]["bbox"] == [34, 100, 90, 112]


def test_multiword_hit_unions_covered_words():
    hits = match_query_in_words(PAGE, "premium is payable", page=3, cap=200)
    assert len(hits) == 1
    assert hits[0]["page"] == 3
    # union of "premium".x0 .. "payable".x1
    assert hits[0]["bbox"] == [34, 100, 160, 112]


def test_case_insensitive():
    assert match_query_in_words(PAGE, "PREMIUM", page=1, cap=200)


def test_no_match_returns_empty():
    assert match_query_in_words(PAGE, "quarterly", page=1, cap=200) == []


def test_cap_is_respected():
    words = [_w("repeat", i * 40, 100, i * 40 + 30, 112) for i in range(10)]
    hits = match_query_in_words(words, "repeat", page=1, cap=3)
    assert len(hits) == 3


def test_empty_query_returns_empty():
    assert match_query_in_words(PAGE, "", page=1, cap=200) == []
