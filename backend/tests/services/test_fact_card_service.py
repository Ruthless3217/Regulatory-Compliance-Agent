import json
from pathlib import Path

import pytest

from app.services.fact_card_service import FactCardService


def _write_card(d: Path, name: str, card: dict) -> None:
    (d / name).write_text(json.dumps(card), encoding="utf-8")


@pytest.fixture
def cards_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cards"
    d.mkdir()
    _write_card(d, "116N198V07-etouch.json", {
        "product_name": "Bajaj Life eTouch II",
        "uin": "116N198V07",
        "rider_uins": ["116B056V01", "116B058V01"],
        "regulatory_descriptor": "A Non-Linked, Non-Participating, Individual Life Insurance Term Plan",
        "compliance_guardrails": {"claims_marketing_must_avoid": ["Do NOT position as investment"]},
    })
    _write_card(d, "116L203V01-goal.json", {
        "product_name": "Bajaj Life LongLife Goal",
        "uin": "116L203V01",
        "rider_uins": [],
    })
    return d


def test_loads_all_products(cards_dir: Path):
    svc = FactCardService(cards_dir)
    products = svc.all_products()
    assert {p["uin"] for p in products} == {"116N198V07", "116L203V01"}
    assert {"Bajaj Life eTouch II", "Bajaj Life LongLife Goal"} == {p["product_name"] for p in products}


def test_get_by_plan_uin(cards_dir: Path):
    svc = FactCardService(cards_dir)
    card = svc.get("116N198V07")
    assert card is not None
    assert card["product_name"] == "Bajaj Life eTouch II"


def test_get_by_rider_uin_returns_parent_card(cards_dir: Path):
    svc = FactCardService(cards_dir)
    card = svc.get("116B056V01")
    assert card is not None
    assert card["uin"] == "116N198V07"


def test_get_missing_returns_none(cards_dir: Path):
    svc = FactCardService(cards_dir)
    assert svc.get("999X999V99") is None


def test_lookup_many_dedups_preserving_order(cards_dir: Path):
    svc = FactCardService(cards_dir)
    cards = svc.lookup_many(["116L203V01", "116N198V07", "116L203V01", "999X999V99"])
    assert [c["uin"] for c in cards] == ["116L203V01", "116N198V07"]


def test_malformed_file_is_skipped(cards_dir: Path):
    (cards_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    svc = FactCardService(cards_dir)  # must not raise
    assert len(svc.all_products()) == 2


def test_missing_dir_yields_empty_service(tmp_path: Path):
    svc = FactCardService(tmp_path / "does_not_exist")
    assert svc.all_products() == []
    assert svc.get("116N198V07") is None


def test_card_without_uin_is_skipped(cards_dir: Path):
    _write_card(cards_dir, "no-uin.json", {"product_name": "Orphan Card"})
    svc = FactCardService(cards_dir)  # must not raise
    assert len(svc.all_products()) == 2
