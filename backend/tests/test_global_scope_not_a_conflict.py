"""Declared `global` scope must not turn a fully-resolved product into a refusal.

SYNTHETIC REPRODUCTION of a deployed failure (2026-08-30): a multi-product
document whose three UINs all resolved cleanly against the fact-card corpus was
refused with `reason=product_unresolved` and marked `needs_review`, discarding a
completed 27-chunk analysis. Nothing about the products was unresolved — the
submission had simply been filed under `product_line="global"`.

These tests use the real fact-card corpus and the real resolver with a synthetic
document. They do NOT replay the production submission, which does not exist
locally.
"""
from pathlib import Path

import pytest

from app.services.agents.compliance.engine import ComplianceEngine
from app.services.agents.graph.nodes import _submission_scope_signals
from app.services.fact_card_service import FactCardService
from app.services.product_resolver import resolve_products, unresolved_product_signals
from app.services.rag.applicability import build_scope, scope_filter_values

# The three UINs the production run resolved, and the categories it derived.
PROD_UINS = ["116L205V01", "116L215V01", "116N186V04"]
PROD_CATEGORIES = {"par", "savings_endowment", "ulip"}

SYNTHETIC_DOC = """Bajaj Life product range — comparison sheet.

Bajaj Life Invest Protect Goal III (UIN: 116L205V01) is a unit linked plan.
Bajaj Life Smart Secure ROP (UIN: 116L215V01) offers return of premium.
Bajaj Life ACE (UIN: 116N186V04) is a participating savings plan.

Guaranteed returns. Market-leading benefits. Invest today.
"""


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(Path(__file__).resolve().parents[1] / "data" / "product_fact_cards")
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture(scope="module")
def matches(cards):
    found = resolve_products(SYNTHETIC_DOC, cards, max_matches=5, min_fuzzy_score=88)
    # Guard the premise: this reproduction is only meaningful while every
    # production UIN still resolves exactly and unambiguously.
    assert [m["uin"] for m in found] == PROD_UINS
    assert all(m["method"] == "uin_regex" and not m["ambiguous"] for m in found)
    return found


def test_production_uins_are_fully_grounded_not_unresolved(cards, matches):
    """The resolver and fact cards are innocent: nothing is actually unresolved."""
    signals = unresolved_product_signals(SYNTHETIC_DOC, cards)

    assert not any(signals.values()), signals
    assert build_scope(matches, cards).categories == PROD_CATEGORIES


def test_global_declaration_with_detected_products_is_not_a_conflict(cards, matches):
    """The production condition: declared global + resolved UINs must not refuse.

    `global` is the universal scope (`applicability._EXPLICIT_GLOBAL`, judged as
    "applies to every product"), not an assertion that the document names none.
    """
    assert _submission_scope_signals("global", matches, cards) == []


def test_global_declaration_admits_a_superset_of_global_only_evidence(cards, matches):
    """Why allowing it is safe: it can only add evidence, never remove it.

    Grading under the detected products' scope accepts everything the
    global-only scope accepted, plus the product-scoped items. A run that is
    strictly better evidenced cannot become a falsely-clean grade.
    """
    with_products = build_scope(matches, cards, declared_product_line="global")
    global_only = build_scope([], cards, declared_product_line="global")

    assert set(scope_filter_values(global_only)) <= set(scope_filter_values(with_products))
    assert with_products.resolved and global_only.resolved


def test_global_and_a_declared_family_build_the_identical_scope(cards, matches):
    """Why blocking it protected nothing: the declaration does not shape scope.

    Once a product is detected, `build_scope` derives categories from the fact
    cards alone. Declaring `ulip` on this par+savings_endowment+ulip document
    was always accepted and produced exactly the scope that declaring `global`
    was refused for.
    """
    as_global = build_scope(matches, cards, declared_product_line="global")
    as_family = build_scope(matches, cards, declared_product_line="ulip")

    assert as_global.categories == as_family.categories
    assert scope_filter_values(as_global) == scope_filter_values(as_family)
    assert _submission_scope_signals("ulip", matches, cards) == []


def test_production_like_run_is_persistable_after_the_fix(cards, matches):
    """End of the chain: the completed analysis is now allowed to be graded."""
    scope_signals = _submission_scope_signals("global", matches, cards)
    unresolved = unresolved_product_signals(SYNTHETIC_DOC, cards)
    if scope_signals:
        unresolved["submission_scope"] = scope_signals

    metadata = {"declared_product_line": "global", "product_match": matches}
    if any(unresolved.values()):
        metadata["degraded"] = "product_unresolved"
        metadata["product_unresolved"] = unresolved

    state = {
        "chunks": [{"id": "c-1", "text": SYNTHETIC_DOC}],
        "violations": [{"description": "Guaranteed returns claim"}],
        "scores": {"overall": 61},
        "status": "completed",
        "metadata": metadata,
    }

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)


# --------------------------------------------------------------------------
# The fail-closed guarantees this must not weaken.
# --------------------------------------------------------------------------


def test_conflicting_declared_family_still_fails_closed(cards, matches):
    """A document filed under one family must not be graded against another."""
    issues = _submission_scope_signals("term", matches, cards)

    assert issues and "conflicts with detected scope" in issues[0]


def test_unsupported_declared_family_still_fails_closed(cards, matches):
    assert _submission_scope_signals("wealth", matches, cards)


def test_no_product_and_no_declaration_still_fails_closed():
    issues = _submission_scope_signals(None, [], None)

    assert issues and "no product was resolved" in issues[0]


def test_unknown_uin_still_fails_closed_under_global(cards):
    """Genuine lack of fact-card grounding is untouched by the global rule."""
    text = SYNTHETIC_DOC + "\nBajaj Life Mystery Plan (UIN: 116N999V01).\n"
    found = resolve_products(text, cards, max_matches=5, min_fuzzy_score=88)
    signals = unresolved_product_signals(text, cards)
    assert signals["unknown_uins"] == ["116N999V01"]

    if scope := _submission_scope_signals("global", found, cards):
        signals["submission_scope"] = scope
    state = {
        "chunks": [{"id": "c-1", "text": text}],
        "status": "completed",
        "metadata": {"degraded": "product_unresolved", "product_unresolved": signals},
    }

    assert ComplianceEngine.evaluate_persistability(state) == (False, "product_unresolved")


# --------------------------------------------------------------------------
# Retrieval-envelope equivalence. The scope tests above compare the SQL
# pushdown values; this compares the applicability judge's actual verdicts,
# which are the decision. Allowing a global filing must admit exactly what the
# detected product categories admit — no broader.
# --------------------------------------------------------------------------

_CANDIDATE_TAGS = [
    "global", "all_products", "child",          # universal / cross-cutting
    "ulip", "par", "savings_endowment",         # the detected categories
    "term", "rider", "group", "pension_annuity", "non_par",  # other families
    "unit-linked", "participating", "endowment",  # aliases of detected ones
    None, "wealth",                             # untagged / unmappable
]


def test_global_admits_exactly_the_detected_product_envelope(cards, matches):
    """Global + detected products must not widen applicability by one item."""
    from app.services.rag.applicability import validate_precedents, validate_rules

    rules = [{"id": f"r{i}", "product_line": tag} for i, tag in enumerate(_CANDIDATE_TAGS)]
    precedents = [
        {"id": f"p{i}", "product_category": tag} for i, tag in enumerate(_CANDIDATE_TAGS)
    ]
    tag_by_rule = {r["id"]: r["product_line"] for r in rules}

    as_global = build_scope(matches, cards, declared_product_line="global")
    as_detected = build_scope(matches, cards, declared_product_line="ulip")

    global_rules, _ = validate_rules(rules, as_global, tag_by_rule)
    detected_rules, _ = validate_rules(rules, as_detected, tag_by_rule)
    global_precs, _ = validate_precedents(precedents, as_global)
    detected_precs, _ = validate_precedents(precedents, as_detected)

    assert {r["id"] for r in global_rules} == {r["id"] for r in detected_rules}
    assert {p["id"] for p in global_precs} == {p["id"] for p in detected_precs}
    # And the envelope is the intended one: the detected families plus the
    # universal tags, with other families and unclassified items still refused.
    assert {r["product_line"] for r in global_rules} == {
        "global", "all_products", "child",
        "ulip", "par", "savings_endowment",
        "unit-linked", "participating", "endowment",
    }


def test_global_with_products_never_admits_an_unrelated_family(cards, matches):
    """The safety direction: resolving products must not open other families."""
    from app.services.rag.applicability import validate_precedents

    unrelated = [{"id": "p-term", "product_category": "term"}]
    accepted, debug = validate_precedents(
        unrelated, build_scope(matches, cards, declared_product_line="global")
    )

    assert accepted == []
    assert debug[0]["verdict"] == "rejected"
    assert "category_conflict" in debug[0]["reason"]
