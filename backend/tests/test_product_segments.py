"""Business market-segment segregation (Par / Term / Non-Par / ULIP).

The declared segregation (backend/data/product_segments.json, provided by the
business 2026-07-28) is the reference; fact-card structural flags are the
runtime ground truth. These tests pin (a) the deterministic derivation
(par = is_participating, ulip = is_unit_linked, term = category,
non_par = neither flag on individual savings/pension) and (b) consistency
between the declared list and every fact card we hold — a declared product
whose card derives a different segment is a data bug, surfaced not silently
absorbed.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.fact_card_service import FactCardService
from app.services.rag.applicability import (
    build_scope,
    derive_segments,
    normalize_category,
)

DATA = Path(__file__).resolve().parents[1] / "data"
CARDS_DIR = DATA / "product_fact_cards"
SEGMENTS_FILE = DATA / "product_segments.json"


# --- vocabulary ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("par", "par"),
    ("Par", "par"),
    ("participating", "par"),
    ("non_par", "non_par"),
    ("Non-Par", "non_par"),
    ("non par", "non_par"),
])
def test_par_nonpar_in_canonical_vocabulary(raw, expected):
    assert normalize_category(raw) == expected


# --- deterministic segment derivation from fact cards --------------------------

def _card(uin):
    svc = FactCardService(CARDS_DIR)
    cards = svc.get_all(uin)
    assert cards, f"no fact card for {uin}"
    return cards[0]


def test_par_product_derives_par_segment():
    # Bajaj Life ACE — declared Par; card has is_participating=true.
    assert "par" in derive_segments(_card("116N186V04"))


def test_nonpar_savings_derives_non_par_segment():
    # Bajaj Life Goal Suraksha — declared Non-Par; neither flag set.
    segs = derive_segments(_card("116N155V19"))
    assert "non_par" in segs
    assert "par" not in segs and "ulip" not in segs


def test_term_product_derives_term_not_nonpar():
    # eTouch II — declared Term; term is its own business segment here.
    segs = derive_segments(_card("116N198V07"))
    assert "term" in segs
    assert "non_par" not in segs


def test_ulip_product_derives_ulip_segment():
    # Fortune Gain — declared ULIP.
    assert "ulip" in derive_segments(_card("116L196V04"))


def test_scope_includes_segments():
    svc = FactCardService(CARDS_DIR)
    scope = build_scope([{"uin": "116N186V04"}], svc)  # ACE (Par)
    assert "par" in scope.categories
    scope2 = build_scope([{"uin": "116N155V19"}], svc)  # Goal Suraksha (Non-Par)
    assert "non_par" in scope2.categories


# --- declared list ↔ fact cards consistency ------------------------------------

_VERSION_SUFFIX = re.compile(
    r"\s+(?:i{1,3}v?|iv|v|vi{0,3}|vii|[0-9]+)$", re.IGNORECASE
)


def _base_name(name):
    s = re.sub(r"\s+", " ", (name or "")).strip().lower()
    prev = None
    while prev != s:
        prev = s
        s = _VERSION_SUFFIX.sub("", s)
    return s


def _load_declared():
    return json.loads(SEGMENTS_FILE.read_text(encoding="utf-8"))


def test_segments_file_exists_and_covers_four_segments():
    data = _load_declared()
    assert set(data["segments"].keys()) == {"par", "term", "non_par", "ulip"}


def test_declared_segments_match_fact_card_derivation():
    """Every declared product that HAS a fact card must derive the declared
    segment from its flags. Missing cards are reported, not failed — they are
    a coverage gap, not an inconsistency."""
    svc = FactCardService(CARDS_DIR)
    cards = []
    seen = set()
    for c in (svc.get_all(u) for u in {p["uin"] for p in svc.all_products()}):
        for card in c:
            if id(card) not in seen:
                seen.add(id(card))
                cards.append(card)

    data = _load_declared()
    mismatches, missing = [], []
    for segment, products in data["segments"].items():
        for p in products:
            names = [p["name"]] + list(p.get("aliases") or [])
            base_names = {_base_name(n) for n in names}
            matched = [
                card for card in cards
                if any(_base_name(card.get("product_name")).startswith(b)
                       for b in base_names)
            ]
            if not matched:
                missing.append(p["name"])
                continue
            for card in matched:
                segs = derive_segments(card)
                if segment not in segs:
                    mismatches.append(
                        f"{p['name']} declared {segment} but card "
                        f"{card.get('uin')} derives {sorted(segs)}"
                    )
    assert not mismatches, "\n".join(mismatches)
    # Coverage gaps are expected (not every product has a card yet) — just
    # make sure the audit surfaces them.
    print(f"\ndeclared products without fact cards: {sorted(missing)}")
