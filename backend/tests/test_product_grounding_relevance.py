"""Which products get fact cards decides which product findings can exist at all.

A fact card is not descriptive context. Tier (P) of the analysis prompt turns it
into findings: "a claim that matches a MUST AVOID item IS a finding; a MUST
SUPPORT claim that is not variant-qualified IS a finding; a missing MUST STATE
element IS a finding", emitted as `product_fact_findings` with a
`product_index`. `_findings_to_violations` discards any finding whose
product_index falls outside the grounded list, so for a product that was
identified but NOT grounded, a product-specific finding is not unlikely — it is
structurally impossible.

That information has no substitute. 91% of the 476 guardrail strings in the
corpus are unique to one product, and the rules corpus is scoped by FAMILY
(global/ulip/rider/...): of 67 seed rules, zero name a UIN and zero name a
product's regulatory descriptor.

Selection was the resolver's rank order, i.e. the order products appear in the
text. Measured over paired documents where the non-compliant claims were
attached to a product listed last, rank order grounded the claim-carrying
product 0/5 times, and family coverage fell as low as 1 family out of 4 in
scope. Ordering candidates by how much the document actually says about them,
then filling the budget for distinct regulatory families, measured 3/5 and
61% family coverage against 0/5 and 53%.
"""
from pathlib import Path

import pytest

from app.config import settings
from app.services.fact_card_service import FactCardService
from app.services.agents.graph.nodes import (
    _grounding_budget_signal,
    _select_grounded_products,
)
from app.services.product_resolver import resolve_products
from app.services.rag.applicability import build_scope

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score
BUDGET = 3

ULIP1 = "116L196V04"     # Fortune Gain II        -> ulip
ULIP2 = "116L204V01"     # Goal Assure IV         -> ulip
ULIP3 = "116L202V01"     # (pure ulip)            -> ulip
TERM = "116N165V01"      # Saral Jeevan Bima      -> term
PAR = "116N127V04"       # Elite Assure           -> par, savings_endowment
PENSION = "116N169V16"   # Saral Pension          -> non_par, pension_annuity
GROUP = "116G133V01"     # Pradhan Mantri JJBY    -> group

CLAIMS = ("\n\nDetailed benefit disclosure\n{n} guarantees returns of 12% every "
          "year with zero risk. Past performance shows 22% per annum. "
          "No lock-in applies to {n}.\n")


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture(scope="module")
def name(cards):
    return {p["uin"]: p["product_name"] for p in cards.all_products()}


def _portfolio(uins, name):
    return "Product Portfolio\n\n" + "\n\n".join(
        f"{name[u]}\nUIN: {u}" for u in uins
    )


def _resolve(text, cards):
    return resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)


def _select(text, cards, budget=BUDGET):
    return _select_grounded_products(_resolve(text, cards), text, cards, budget)


def _families(uins, cards):
    return set(build_scope([{"uin": u} for u in uins], cards).categories)


# --------------------------------------------------------------------------
# Budget and determinism.
# --------------------------------------------------------------------------


def test_single_product_is_grounded(cards, name):
    assert _select(_portfolio([ULIP1], name), cards) == [ULIP1]


def test_three_products_are_all_grounded(cards, name):
    text = _portfolio([ULIP1, TERM, PAR], name)

    assert set(_select(text, cards)) == {ULIP1, TERM, PAR}


def test_selection_never_exceeds_the_budget(cards, name):
    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)

    assert len(_select(text, cards)) == BUDGET


def test_selection_is_deterministic(cards, name):
    text = _portfolio([ULIP1, ULIP2, ULIP3, TERM, PAR], name)

    assert len({tuple(_select(text, cards)) for _ in range(5)}) == 1


def test_selection_only_ever_returns_identified_products(cards, name):
    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)
    identified = {m["uin"] for m in _resolve(text, cards)}

    assert set(_select(text, cards)) <= identified


# --------------------------------------------------------------------------
# Relevance: the product the document actually makes claims about.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", [PAR, ULIP1, PENSION])
def test_the_product_carrying_the_claims_is_grounded(target, cards, name):
    """Rank order grounded this product 0/5 times; it is listed LAST."""
    others = [u for u in [ULIP1, TERM, PAR, GROUP, PENSION] if u != target]
    text = (_portfolio(others, name) + f"\n\n{name[target]}\nUIN: {target}"
            + CLAIMS.format(n=name[target]))

    assert target in _select(text, cards)


def test_a_passing_mention_does_not_displace_the_documented_product(cards, name):
    """Low-relevance product must not consume a grounding slot."""
    text = (f"{name[ULIP1]}\nUIN: {ULIP1}" + CLAIMS.format(n=name[ULIP1])
            + f"\n\nAlso available: {name[TERM]} (UIN {TERM}), "
              f"{name[GROUP]} (UIN {GROUP}), {name[PENSION]} (UIN {PENSION}).")

    assert ULIP1 in _select(text, cards)


# --------------------------------------------------------------------------
# Family coverage: the regulatory obligations actually in scope.
# --------------------------------------------------------------------------


def test_grounding_covers_distinct_families_not_three_of_one(cards, name):
    """Three ULIPs first: rank order grounded 1 family of the 4 in scope."""
    text = _portfolio([ULIP1, ULIP2, ULIP3, TERM, PAR], name)
    picked = _select(text, cards)
    scope = _families([m["uin"] for m in _resolve(text, cards)], cards)

    assert _families(picked, cards) & scope == scope


def test_family_coverage_is_never_worse_than_rank_order(cards, name):
    """Guard against a selection change that trades coverage away."""
    documents = [
        _portfolio([ULIP1, TERM, PAR, GROUP], name),
        _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name),
        _portfolio([ULIP1, ULIP2, ULIP3, TERM, PAR], name),
    ]
    for text in documents:
        matches = _resolve(text, cards)
        scope = _families([m["uin"] for m in matches], cards)
        rank_order = [m["uin"] for m in matches][:BUDGET]
        chosen = _select(text, cards)

        assert len(_families(chosen, cards) & scope) >= len(
            _families(rank_order, cards) & scope
        ), text[:60]


# --------------------------------------------------------------------------
# Everything the earlier fixes established.
# --------------------------------------------------------------------------


def test_exact_uins_keep_priority_over_fuzzy_matches(cards, name):
    """A fuzzy name mentioned more often must not outrank deterministic identity."""
    text = (f"{name[TERM]}. {name[TERM]}. {name[TERM]}.\n\nUIN: {ULIP1}\n")
    matches = _resolve(text, cards)
    exact = [m["uin"] for m in matches if m["method"] == "uin_regex"]
    picked = _select(text, cards)

    assert set(exact) <= set(picked)


def test_generic_corporate_document_grounds_nothing(cards):
    text = ("Bajaj Life Insurance is committed to helping customers protect "
            "their financial future. Contact Bajaj Life Insurance for details.")

    assert _resolve(text, cards) == []
    assert _select(text, cards) == []


def test_scope_still_covers_every_identified_product(cards, name):
    """Grounding selection must never narrow the regulatory envelope."""
    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)
    matches = _resolve(text, cards)
    picked = _select(text, cards)

    assert len(picked) == BUDGET < len(matches)
    assert _families([m["uin"] for m in matches], cards) == set(
        build_scope(matches, cards).categories
    )


# --------------------------------------------------------------------------
# Auditability.
# --------------------------------------------------------------------------


def test_audit_record_names_which_products_were_grounded(cards, name):
    """"Which were grounded?" was previously answerable only as a count."""
    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)
    matches = _resolve(text, cards)
    picked = _select(text, cards)
    signal = _grounding_budget_signal(matches, BUDGET, picked)

    assert signal["detected"] == len(matches)
    assert signal["grounded"] == BUDGET
    assert signal["grounded_uins"] == sorted(picked)
    assert signal["not_grounded"] == sorted(
        {m["uin"] for m in matches} - set(picked)
    )
    assert signal["selection"] == "mentions_then_family_coverage"


def test_audit_record_reports_families_left_ungrounded(cards, name):
    """With 7 families in scope and a budget of 3, some are always uncovered."""
    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)
    matches = _resolve(text, cards)
    signal = _grounding_budget_signal(matches, BUDGET, _select(text, cards), cards)
    scope = _families([m["uin"] for m in matches], cards)

    assert set(signal["families_not_grounded"]) == scope - _families(
        signal["grounded_uins"], cards
    )
    assert signal["families_not_grounded"], "premise: budget cannot cover 7 families"


def test_no_audit_record_when_every_product_is_grounded(cards, name):
    matches = _resolve(_portfolio([ULIP1, TERM], name), cards)

    assert _grounding_budget_signal(matches, BUDGET, [ULIP1, TERM]) is None


def test_the_selected_products_are_the_ones_injected(cards, name, monkeypatch):
    """The audit must describe the prompt that was actually built."""
    import asyncio

    from app.services import fact_card_service as fcs
    from app.services.agents.graph import nodes as graph_nodes

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(settings, "product_match_max", BUDGET)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)

    text = _portfolio([ULIP1, TERM, PAR, GROUP, PENSION], name)
    matches = _resolve(text, cards)
    picked = _select(text, cards)

    facts, _ = asyncio.run(graph_nodes._resolve_product_grounding(
        {"metadata": {"product_match": matches, "product_grounding_uins": picked}}, []
    ))

    assert [c["uin"] for c in facts] == picked
