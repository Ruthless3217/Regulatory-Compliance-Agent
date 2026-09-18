"""Naming a product means saying its name, not scattering its words over 25KB.

The identity-token gate required a product's distinctive tokens to be a SUBSET of
the document's tokens, with no locality constraint. That holds on short fixtures
and collapses on real collateral: in the 25KB "GEO content for Smart Secure"
document the tokens `secure` (x52), `term` (x51), `smart` (x44), `protection`
(x19) scatter across unrelated sentences, and seven products the document never
names satisfied the rule. One of them, 116L214V01 Smart Wealth Goal VI, is a
three-variant collision UIN, so a fabricated match also fabricated an ambiguity
and failed the whole run closed.

Measured on that document, the separation is not marginal:

    real product named in the doc     min token span   2
    nearest accidental co-occurrence  min token span  18
    longest legitimate product name   needs span       7   (all 44 products)

So identity evidence must be LOCAL. Every identity token must appear inside one
bounded window. The window band [8, 15] gives 44/44 recall and zero false
matches on the real document; it breaks at 18. `_IDENTITY_WINDOW_TOKENS = 12`
sits in the middle of that band.

Exact UIN matching is untouched: it is a separate, stronger tier.
"""
import re
from pathlib import Path

import pytest

from app.config import settings
from app.services.fact_card_service import FactCardService
from app.services.product_resolver import resolve_products

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score

# A long document whose vocabulary is dense in the words product names are built
# from, but which names no product. Mirrors the real collateral's distribution.
SCATTER = (
    "Our smart approach helps you secure your family's future with a protection "
    "plan that returns value over the long term. The group of benefits below is "
    "guaranteed subject to policy terms. Wealth creation is a goal for every "
    "policyholder, and the shield of life cover protects that goal. Term options "
    "are flexible; the return of premium variant secures the maturity benefit. "
) * 20

VERSION = re.compile(r"^(?:i{1,3}v?|iv|vi{0,3}|vii|v|\d{1,2})$", re.I)


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


def _resolve(text, cards):
    return resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)


def _uins(matches):
    return {m["uin"] for m in matches}


# --------------------------------------------------------------------------
# The defect: scattered tokens must not fabricate a product.
# --------------------------------------------------------------------------


def test_a_long_document_of_scattered_identity_words_names_no_product(cards):
    assert _resolve(SCATTER, cards) == []


def test_scattered_tokens_do_not_fabricate_an_ambiguous_product(cards):
    """116L214V01 is a 3-variant UIN: a false match also fakes an ambiguity."""
    matches = _resolve(SCATTER, cards)

    assert not [m for m in matches if m.get("ambiguous")]


@pytest.mark.parametrize("name", [
    "Bajaj Life Smart Wealth Goal VI",
    "Bajaj Life Group Secure Shield",
    "Bajaj Life Group Secure Return",
    "Bajaj Life Smart Protection Goal",
    "Bajaj Life Guaranteed Wealth Goal",
    "Bajaj Life Group Term Life",
])
def test_named_false_positives_from_the_real_document_are_gone(name, cards):
    """Each of these was resolved from the real 25KB document, which never
    contains the name as a phrase."""
    resolved = {m["product_name"] for m in _resolve(SCATTER, cards)}

    assert name not in resolved


def test_words_in_different_sentences_do_not_make_a_product(cards):
    """The tokens of one real product, deliberately kept far apart."""
    gap = " ".join(["filler words about policy servicing and premiums."] * 40)
    text = f"Our smart plan. {gap} Wealth is important. {gap} Every goal matters."

    assert not [m for m in _resolve(text, cards)
                if m["product_name"] == "Bajaj Life Smart Wealth Goal VI"]


# --------------------------------------------------------------------------
# Recall: every legitimate formulation must still resolve, in a LONG document.
# --------------------------------------------------------------------------


def _embedded(mention):
    """The mention inside a long, token-dense document — the realistic case."""
    return SCATTER + "\n\n" + mention + "\n\n" + SCATTER[:3000]


@pytest.mark.parametrize("form", [
    "full", "in_sentence", "no_brand", "no_version", "punctuation", "whitespace",
])
def test_every_product_resolves_from_every_legitimate_formulation(form, cards):
    by_uin = {p["uin"]: p for p in cards.all_products()}
    misses = []
    for product in cards.all_products():
        name = product["product_name"]
        # A superseded card resolves as its successor (same name, current UIN).
        expected = (product["superseded_by"]
                    if product.get("superseded_by") in by_uin else product["uin"])
        mention = {
            "full": name,
            "in_sentence": f"Presenting {name}, a plan designed for your family.",
            "no_brand": re.sub(r"^\s*bajaj\s+life\s*", "", name, flags=re.I),
            "no_version": " ".join(t for t in name.split() if not VERSION.match(t)),
            "punctuation": f"{name} - A Non-Linked Individual Plan (see brochure).",
            "whitespace": re.sub(r"\s+", "   ", name),
        }[form]
        if expected not in _uins(_resolve(_embedded(mention), cards)):
            misses.append(product["uin"])

    # 116N186V04 "Bajaj Life ACE" reduces to the bare token "ACE" once the brand
    # is stripped, and the fuzzy tier will not carry a three-letter residue in a
    # document that never says "Bajaj Life". Pre-existing and unrelated to
    # locality — it behaves identically before and after this change — but named
    # here so any NEW recall loss still fails this test.
    allowed = {"116N186V04"} if form == "no_brand" else set()

    assert set(misses) <= allowed, f"unexpected recall loss: {sorted(set(misses) - allowed)}"


def test_a_product_named_only_at_the_very_end_of_a_long_document_resolves(cards):
    text = SCATTER + "\n\nBajaj Life Smart Secure ROP"

    assert "116L215V01" in _uins(_resolve(text, cards))


def test_a_name_split_across_a_line_break_still_resolves(cards):
    text = SCATTER + "\n\nBajaj Life Smart\nSecure ROP\n\n" + SCATTER[:2000]

    assert "116L215V01" in _uins(_resolve(text, cards))


def test_a_short_intervening_qualifier_inside_the_name_still_resolves(cards):
    """Real collateral inserts words; the window must tolerate a few."""
    text = SCATTER + "\n\nBajaj Life Smart Secure (ROP) plan\n\n" + SCATTER[:2000]

    assert "116L215V01" in _uins(_resolve(text, cards))


# --------------------------------------------------------------------------
# Exact UIN remains the authoritative tier, untouched.
# --------------------------------------------------------------------------


def test_exact_uin_resolves_with_no_name_evidence_anywhere(cards):
    matches = _resolve(SCATTER + "\n\nUIN: 116L215V01\n\n" + SCATTER[:2000], cards)
    exact = [m for m in matches if m["uin"] == "116L215V01"]

    assert exact and exact[0]["method"] == "uin_regex"
    assert exact[0]["confidence"] == 1.0


def test_exact_uin_is_ranked_before_name_matches(cards):
    text = (SCATTER + "\n\nBajaj Life Invest Protect Goal III.\n\nUIN: 116N186V04\n\n"
            + SCATTER[:2000])
    matches = _resolve(text, cards)

    assert matches[0]["method"] == "uin_regex"
    assert matches[0]["uin"] == "116N186V04"


def test_unknown_uin_still_fails_closed(cards):
    from app.services.product_resolver import unresolved_product_signals

    signals = unresolved_product_signals(SCATTER + " Mystery plan UIN 116N999V01.", cards)

    assert signals["unknown_uins"] == ["116N999V01"]


def test_rider_uin_without_a_fact_card_still_fails_closed(cards):
    """The real document's genuine refusal must survive the precision fix."""
    from app.services.product_resolver import unresolved_product_signals

    # 116N216V01 has its own card since 2026-09-18; 116B036V02 (cited by six
    # parent cards, no card of its own) reproduces the genuine gap.
    signals = unresolved_product_signals(
        SCATTER + " Health Shield Rider (UIN:116B036V02).", cards
    )

    assert signals["rider_uins_without_fact_cards"] == ["116B036V02"]


def test_ambiguous_uin_is_still_flagged_when_genuinely_named(cards):
    matches = _resolve("Product UIN: 116L214V01 applies.", cards)
    entry = next(m for m in matches if m["uin"] == "116L214V01")

    assert entry["ambiguous"] is True
    assert len(entry["candidates"]) > 1


# --------------------------------------------------------------------------
# Generic corporate precision is unchanged.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Bajaj Life",
    "Bajaj Life Insurance Limited",
    "Bajaj Life is committed to helping customers secure their future.",
    "Insurance is the subject matter of solicitation. Terms and conditions apply.",
])
def test_generic_corporate_text_still_resolves_to_no_product(text, cards):
    assert _resolve(text, cards) == []


def test_marketing_alias_still_resolves(cards):
    text = SCATTER + "\n\nThe Health Management Services table lists the benefits.\n"

    assert "116N198V07" in _uins(_resolve(text, cards))


def test_short_acronym_alias_still_resolves_on_a_word_boundary(cards):
    assert "116N198V07" in _uins(_resolve("Benefits under HMS are included.", cards))
