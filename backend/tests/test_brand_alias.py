"""Brand/entity alias handling (Bajaj Allianz Life → Bajaj Life rename).

The 2025 rename was applied as a textual find-and-replace of 'Allianz', which
gutted the brochure parser's regexes (``(?:\\s+)?`` is the residue of
``(?:\\s+Allianz)?``): legacy-branded PDFs lost their product names and the
company-name rejection stopped rejecting the former legal name. Historical
creatives and brochures filed under the former name remain valid regulatory
records — parsing must be brand-invariant, driven by a canonical entity
registry with aliases, never by blind string replacement.
"""
import re

import pytest

from app.services.brochure_parser import _COMPANY_NAME_RE, _PRODUCT_PHRASE_RE
from app.services.entity_registry import EntityRegistry, get_entity_registry


# --- parser regexes are brand-invariant ---------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Bajaj Life Smart Wealth Goal", "Smart Wealth Goal"),
    ("Bajaj Allianz Life Smart Wealth Goal", "Smart Wealth Goal"),
])
def test_product_phrase_matches_both_brand_eras(text, expected):
    m = _PRODUCT_PHRASE_RE.search(text)
    assert m is not None, f"no product match in {text!r}"
    assert m.group(1).startswith(expected)


@pytest.mark.parametrize("text", [
    "Bajaj Life Insurance Company Limited",
    "Bajaj Allianz Life Insurance Company Limited",
])
def test_company_legal_name_rejected_both_eras(text):
    assert _COMPANY_NAME_RE.search(text), f"company name not rejected: {text!r}"


# --- canonical entity registry -------------------------------------------------

def test_registry_loads_canonical_entity():
    reg = get_entity_registry()
    e = reg.get("balic")
    assert e is not None
    assert e["canonical_name"] == "Bajaj Life Insurance Limited"


def test_former_name_is_a_known_alias_not_a_stranger():
    reg = get_entity_registry()
    aliases = {a["name"] for a in reg.aliases("balic")}
    assert "Bajaj Allianz Life Insurance Company Limited" in aliases


def test_alias_regex_matches_every_era():
    reg = get_entity_registry()
    rx = reg.alias_regex("balic")
    for s in ("Bajaj Life Insurance", "Bajaj Allianz Life Insurance",
              "Bajaj Allianz Life", "bajaj life"):
        assert re.search(rx, s, re.IGNORECASE), f"alias regex misses {s!r}"


def test_prohibited_customer_facing_aliases_are_flagged():
    reg = get_entity_registry()
    prohibited = {a["name"] for a in reg.aliases("balic")
                  if a.get("status") == "prohibited_customer_facing"}
    assert "BALIC" in prohibited


# --- data hygiene: the rename must not have created self-referential text ------

def test_disclaimer_footers_name_the_actual_former_entity():
    """'X (Formerly known as X)' is a legal tautology introduced by the blind
    find-and-replace; the parenthetical must cite the former name."""
    from pathlib import Path
    import json
    d = Path(__file__).resolve().parents[1] / "data" / "disclaimers"
    for p in d.glob("*.json"):
        text = json.loads(p.read_text(encoding="utf-8"))["text"]
        assert "Bajaj Life Insurance Limited (Formerly known as Bajaj Life Insurance Limited)" \
            not in text, f"{p.name}: self-referential 'formerly known as'"


def test_fact_cards_do_not_call_current_brand_legacy():
    from pathlib import Path
    import json
    d = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
    for p in d.glob("*.json"):
        raw = p.read_text(encoding="utf-8")
        assert "legacy 'Bajaj Life Insurance'" not in raw, (
            f"{p.name}: asserts the CURRENT name is legacy branding"
        )
