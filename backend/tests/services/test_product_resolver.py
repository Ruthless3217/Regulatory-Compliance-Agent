from app.services.product_resolver import resolve_products


class _FakeCards:
    def __init__(self, products):
        self._p = products
    def all_products(self):
        return self._p


CARDS = _FakeCards([
    {"uin": "116N198V07", "product_name": "Bajaj Life eTouch II"},
    {"uin": "116L203V01", "product_name": "Bajaj Life LongLife Goal"},
])


def test_detects_uin_by_regex():
    text = "Presenting eTouch with UIN 116N198V07, a term plan."
    out = resolve_products(text, CARDS, max_matches=3, min_fuzzy_score=60)
    assert any(m["uin"] == "116N198V07" and m["method"] == "uin_regex" for m in out)


def test_no_match_returns_empty():
    out = resolve_products("Generic insurance ad with no identifiers.", CARDS,
                           max_matches=3, min_fuzzy_score=95)
    assert out == []


def test_fuzzy_name_match_when_no_uin():
    text = "Invest in the LongLife Goal plan from Bajaj for lifelong income."
    out = resolve_products(text, CARDS, max_matches=3, min_fuzzy_score=60)
    assert any(m["uin"] == "116L203V01" and m["method"] == "name_fuzzy" for m in out)


def test_uin_match_ranks_before_fuzzy():
    text = "eTouch II 116N198V07 vs the LongLife Goal plan."
    out = resolve_products(text, CARDS, max_matches=3, min_fuzzy_score=60)
    assert out[0]["method"] == "uin_regex"


def test_caps_at_max_matches():
    cards = _FakeCards([
        {"uin": "116N198V07", "product_name": "Plan A"},
        {"uin": "116L203V01", "product_name": "Plan B"},
        {"uin": "116N999V01", "product_name": "Plan C"},
        {"uin": "116N888V01", "product_name": "Plan D"},
    ])
    text = "116N198V07 116L203V01 116N999V01 116N888V01"
    out = resolve_products(text, cards, max_matches=3, min_fuzzy_score=60)
    assert len(out) == 3


def test_unknown_uin_in_text_is_ignored():
    out = resolve_products("Random code 999X999V99 here.", CARDS,
                           max_matches=3, min_fuzzy_score=95)
    assert out == []
