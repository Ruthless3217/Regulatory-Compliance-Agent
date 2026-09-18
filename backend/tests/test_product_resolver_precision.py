"""Generic corporate language must not establish product identity.

Every fact-card product name begins "Bajaj Life ...", and `bajaj`/`life` occur in
100% of them. `fuzz.partial_ratio` slides the shorter string over the longer, so
the bare brand is an EXACT substring of every product name and scored 100 — the
same score an exact full-name match gets. Twelve generic corporate inputs each
resolved to three products, none flagged ambiguous, at the production threshold
(kb_min_fuzzy_score=60, product_match_max=3).

That is a regulatory-scope contamination, not a cosmetic one: a resolved scope
switches `scope_filter_values` from "no filter" to a SQL cut, so a fabricated
identity both admits the wrong families' rules and EXCLUDES rows that would
otherwise have been retrieved.

These run against the real 44-card corpus and the real production config.
"""
import re
from pathlib import Path

import pytest

from app.config import settings
from app.services.fact_card_service import FactCardService
from app.services.product_resolver import resolve_products
from app.services.rag.applicability import build_scope

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MAX_MATCHES = settings.product_match_max
MIN_FUZZY = settings.kb_min_fuzzy_score

GENERIC_INPUTS = [
    "Bajaj Life",
    "Bajaj Life Insurance",
    "Bajaj Life Insurance Limited",
    "Bajaj Life Insurance Ltd",
    "Bajaj Life product",
    "Bajaj Life plan",
    "Bajaj Life benefits",
    "Bajaj Life website",
    "Bajaj Life offers protection",
    "Bajaj Life is committed to helping customers secure their future.",
    "Bajaj Life offers insurance products and financial solutions.",
    "Contact Bajaj Life Insurance for more information.",
    "Insurance is the subject matter of solicitation. Terms and conditions apply.",
    "For more information visit our website or call our customer care team.",
]

_VERSION = re.compile(r"^(?:i{1,3}v?|iv|vi{0,3}|vii|v|\d{1,2})$", re.I)


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


def _resolve(text, cards):
    return resolve_products(text, cards, max_matches=MAX_MATCHES, min_fuzzy_score=MIN_FUZZY)


# --------------------------------------------------------------------------
# Precision — the defect.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", GENERIC_INPUTS)
def test_generic_corporate_text_resolves_to_no_product(text, cards):
    assert _resolve(text, cards) == []


def test_bare_brand_never_scores_like_an_exact_match(cards):
    """"Bajaj Life" used to return three products at confidence 1.000."""
    assert _resolve("Bajaj Life", cards) == []


def test_no_generic_input_resolves_to_any_product_corpus_wide(cards):
    offenders = {t: _resolve(t, cards) for t in GENERIC_INPUTS}
    assert not {t: m for t, m in offenders.items() if m}


# --------------------------------------------------------------------------
# Recall — every product must still resolve, from every realistic phrasing.
# --------------------------------------------------------------------------


def _named(matches, uin):
    return uin in {m["uin"] for m in matches}


@pytest.mark.parametrize("form", ["full", "short", "no_version", "sentence"])
def test_every_product_still_resolves_from_its_own_name(form, cards):
    """Every product, four phrasings each — no product may become unmatchable.
    A superseded card (116N187V09 -> V11) is expected to resolve as its
    successor: same product name, current approved UIN."""
    by_uin = {p["uin"]: p for p in cards.all_products()}
    misses = []
    for product in cards.all_products():
        name, uin = product["product_name"], product["uin"]
        if product.get("superseded_by") in by_uin:
            uin = product["superseded_by"]
        text = {
            "full": name,
            "short": re.sub(r"^\s*bajaj\s+life\s*", "", name, flags=re.I),
            "no_version": " ".join(t for t in name.split() if not _VERSION.match(t)),
            "sentence": f"Presenting {name}, a plan designed for your family.",
        }[form]
        if not _named(_resolve(text, cards), uin):
            misses.append((uin, name, text))
    assert not misses


def test_exact_uin_is_unaffected_by_the_name_gate(cards):
    matches = _resolve("Product UIN: 116L215V01 applies.", cards)

    assert matches[0]["uin"] == "116L215V01"
    assert matches[0]["method"] == "uin_regex"
    assert matches[0]["confidence"] == 1.0


def test_uin_resolves_even_when_the_name_is_absent(cards):
    """The UIN tier is the exact tier; it must never depend on name evidence."""
    assert _named(_resolve("See 116N186V04 for details.", cards), "116N186V04")


def test_marketing_alias_still_resolves_its_product(cards):
    # eTouch II is the only card carrying marketing_aliases.
    matches = _resolve(
        "The Health Management Services table lists every included benefit.", cards
    )

    assert _named(matches, "116N198V07")


def test_short_acronym_alias_still_resolves_on_a_word_boundary(cards):
    assert _named(_resolve("Benefits under HMS are included.", cards), "116N198V07")


def test_acronym_alias_does_not_fire_inside_another_word(cards):
    assert not _named(_resolve("Twelve months of cover.", cards), "116N198V07")


# --------------------------------------------------------------------------
# System level — a generic document must not acquire product-specific scope.
# --------------------------------------------------------------------------

GENERIC_DOC = """Bajaj Life Insurance is committed to helping customers
protect their financial future.

Please contact Bajaj Life Insurance for further details.
"""

SPECIFIC_DOC = """Bajaj Life Smart Secure ROP
UIN: 116L215V01
Return of premium on maturity.
"""


def test_generic_document_gains_no_product_scope(cards):
    matches = _resolve(GENERIC_DOC, cards)
    scope = build_scope(matches, cards, declared_product_line="global")

    assert matches == []
    assert scope.uins == frozenset()
    # Previously: {'par', 'savings_endowment', 'term'} — invented from three
    # products the document never names.
    assert scope.categories == frozenset()


def test_generic_document_does_not_narrow_a_declared_family(cards):
    """The fabricated scope also EXCLUDED rows; the declared scope must survive."""
    scope = build_scope(_resolve(GENERIC_DOC, cards), cards, declared_product_line="term")

    assert scope.categories == frozenset({"term"})


def test_specific_document_still_resolves_exactly_its_product(cards):
    matches = _resolve(SPECIFIC_DOC, cards)
    scope = build_scope(matches, cards, declared_product_line="global")

    assert matches[0]["uin"] == "116L215V01"
    assert matches[0]["method"] == "uin_regex"
    # Previously also dragged in ACE (0.88) and Smart Pension (0.86), inflating
    # the envelope to {par, pension_annuity, savings_endowment, ulip}.
    assert scope.uins == frozenset({"116L215V01"})
    assert scope.categories == frozenset({"ulip"})
