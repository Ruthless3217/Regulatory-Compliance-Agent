"""Duplicate-UIN handling (Phase-4: expose ambiguity, never silently pick).

Real collisions exist in backend/data/product_fact_cards: 116L211V02 (Supreme
Gold vs Horizon) and 116L214V01 (three Smart Wealth Goal VI variants whose
`offers_guaranteed_benefits` flags disagree). Loading is last-file-wins with no
trace, and the fuzzy resolver could emit the same UIN once per variant card,
burning the product-match budget. These tests pin the fix: collisions are
detected and reported, resolution marks ambiguity, and one UIN consumes one slot.
"""
import json

import pytest

from app.services.fact_card_service import FactCardService
from app.services.product_resolver import resolve_products, unresolved_product_signals

UIN_DUP = "116L214V01"
UIN_UNIQUE = "116L999V01"


@pytest.fixture()
def cards_dir(tmp_path):
    cards = [
        ("a-swg-wealth.json", {
            "uin": UIN_DUP, "product_name": "Bajaj Life Smart Wealth Goal VI (Wealth Variant)",
            "offers_guaranteed_benefits": False,
        }),
        ("b-swg-child.json", {
            "uin": UIN_DUP, "product_name": "Bajaj Life Smart Wealth Goal VI (Child Wealth Variant)",
            "offers_guaranteed_benefits": True,
        }),
        ("c-unique.json", {
            "uin": UIN_UNIQUE, "product_name": "Bajaj Life Elite Assure",
        }),
    ]
    for name, payload in cards:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_collisions_are_detected_and_reported(cards_dir):
    svc = FactCardService(cards_dir)
    assert UIN_DUP in svc.collisions
    assert UIN_UNIQUE not in svc.collisions
    names = {c["product_name"] for c in svc.collisions[UIN_DUP]}
    assert len(names) == 2


def test_get_all_returns_every_colliding_record(cards_dir):
    svc = FactCardService(cards_dir)
    assert len(svc.get_all(UIN_DUP)) == 2
    assert len(svc.get_all(UIN_UNIQUE)) == 1
    assert svc.get_all("missing") == []


def test_declared_catalog_exposes_products_missing_fact_cards():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    missing = {
        product["name"]
        for product in FactCardService(real_cards).declared_without_fact_cards
    }

    assert missing == {
        "Bajaj Life ACE Advantage",
        "Bajaj Life FIG Plus",
        "Bajaj Life Gain",
        "Bajaj Life GBS III",
    }


def test_unknown_uin_and_declared_missing_product_are_unresolved():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    svc = FactCardService(real_cards)
    signals = unresolved_product_signals(
        "Bajaj Life ACE Advantage, product UIN 116N999V01",
        svc,
    )

    assert signals["unknown_uins"] == ["116N999V01"]
    assert signals["declared_products_without_fact_cards"] == [
        "Bajaj Life ACE Advantage"
    ]


def test_unknown_uin_detection_is_case_insensitive_and_normalized():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    signals = unresolved_product_signals(
        "product uin 116n999v01",
        FactCardService(real_cards),
    )

    assert signals["unknown_uins"] == ["116N999V01"]


def test_lowercase_known_uin_resolves_to_canonical_uppercase(cards_dir):
    matches = resolve_products(
        f"product uin {UIN_UNIQUE.lower()}",
        FactCardService(cards_dir),
        max_matches=3,
        min_fuzzy_score=60,
    )

    assert matches[0]["uin"] == UIN_UNIQUE
    assert matches[0]["method"] == "uin_regex"


def test_rider_only_uin_without_standalone_card_routes_to_review():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    svc = FactCardService(real_cards)
    signals = unresolved_product_signals("Rider UIN 116A057V02", svc)

    assert signals["unknown_uins"] == []
    assert signals["rider_uins_without_fact_cards"] == ["116A057V02"]
    assert "116A057V02" in svc.rider_parent_collisions


def test_standalone_rider_card_is_not_treated_as_a_parent_mapping():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    signals = unresolved_product_signals(
        "Rider UIN 116A059V01",
        FactCardService(real_cards),
    )

    assert signals["rider_uins_without_fact_cards"] == []


def test_missing_fact_card_directory_reports_unavailable(tmp_path):
    svc = FactCardService(tmp_path / "missing")

    assert "cards_dir_missing" in svc.availability_issues
    assert "cards_unavailable" in svc.availability_issues


def test_missing_product_name_requires_a_complete_name_boundary():
    from pathlib import Path

    real_cards = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    signals = unresolved_product_signals(
        "Bajaj Life Gainer is unrelated.",
        FactCardService(real_cards),
    )

    assert signals["declared_products_without_fact_cards"] == []


def test_get_refuses_a_colliding_uin_instead_of_picking_one(cards_dir):
    """This used to assert `get(UIN_DUP) is not None` on the claim that
    "callers depend on it". The consumer audit (2026-09-11) found none did:
    build_scope and the disclaimer triggers use get_all(), and the only get()
    caller was an unapplied script. What the old contract DID do was pick the
    last-sorted variant — on the real 116L214V01 that is the one card with
    offers_guaranteed_benefits=False, so filename order decided whether
    "guaranteed" wording was legal. A unique UIN still resolves; a colliding
    one resolves to nothing and names its candidates."""
    svc = FactCardService(cards_dir)

    assert svc.get(UIN_DUP) is None
    resolution = svc.resolve_one(UIN_DUP)
    assert resolution.ambiguous is True
    assert len(resolution.candidates) == 2
    assert svc.get(UIN_UNIQUE)["product_name"] == "Bajaj Life Elite Assure"


def test_collision_logged_at_load(cards_dir, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        FactCardService(cards_dir)
    assert any(UIN_DUP in r.message and "collision" in r.message.lower()
               for r in caplog.records)


# --- resolver: one UIN, one budget slot, ambiguity exposed ---------------------

def test_fuzzy_match_emits_each_uin_once(cards_dir):
    svc = FactCardService(cards_dir)
    text = ("Presenting Smart Wealth Goal VI Wealth Variant and the "
            "Smart Wealth Goal VI Child Wealth Variant for your family. "
            "Also consider Elite Assure for guaranteed savings.")
    matches = resolve_products(text, svc, max_matches=3, min_fuzzy_score=60)
    uins = [m["uin"] for m in matches]
    assert uins.count(UIN_DUP) == 1, "duplicate cards must not burn budget slots"
    assert UIN_UNIQUE in uins, "distinct product must survive within the budget"


def test_ambiguous_uin_is_marked_with_candidates(cards_dir):
    svc = FactCardService(cards_dir)
    matches = resolve_products(f"Product UIN: {UIN_DUP}.", svc,
                               max_matches=3, min_fuzzy_score=60)
    m = next(m for m in matches if m["uin"] == UIN_DUP)
    assert m["ambiguous"] is True
    assert len(m["candidates"]) == 2


def test_unique_uin_is_not_marked_ambiguous(cards_dir):
    svc = FactCardService(cards_dir)
    matches = resolve_products(f"Try Elite Assure (UIN {UIN_UNIQUE}).", svc,
                               max_matches=3, min_fuzzy_score=60)
    m = next(m for m in matches if m["uin"] == UIN_UNIQUE)
    assert m.get("ambiguous", False) is False


# --- marketing-alias resolution (feature collateral without a product name) ----

@pytest.fixture()
def cards_with_alias(tmp_path):
    (tmp_path / "etouch.json").write_text(json.dumps({
        "uin": "116N198V07", "product_name": "Bajaj Life eTouch II",
        "marketing_aliases": ["Health Management Services", "HMS"],
    }), encoding="utf-8")
    (tmp_path / "other.json").write_text(json.dumps({
        "uin": UIN_UNIQUE, "product_name": "Bajaj Life Elite Assure",
    }), encoding="utf-8")
    return tmp_path


def test_alias_resolves_feature_collateral_to_product(cards_with_alias):
    """HMS collateral (e.g. Health-Management-Services-for-Women-2.pdf) carries
    no product name or UIN — the marketing alias must ground it to eTouch II."""
    svc = FactCardService(cards_with_alias)
    text = ("Health Management Services for Women\n"
            "Comprehensive Health Check-Up: Cancer Screening ... OPD wallet")
    matches = resolve_products(text, svc, max_matches=3, min_fuzzy_score=60)
    m = next((m for m in matches if m["uin"] == "116N198V07"), None)
    assert m is not None, "alias did not resolve"
    assert m["method"] == "alias_match"


def test_short_alias_requires_word_boundary(cards_with_alias):
    svc = FactCardService(cards_with_alias)
    hit = resolve_products("Enrol in our HMS package today.", svc,
                           max_matches=3, min_fuzzy_score=60)
    assert any(m["uin"] == "116N198V07" for m in hit)
    # 'months' must NOT trigger the 3-letter alias.
    miss = resolve_products("Benefits vest in 6 months time.", svc,
                            max_matches=3, min_fuzzy_score=60)
    assert not any(m["uin"] == "116N198V07" for m in miss)
