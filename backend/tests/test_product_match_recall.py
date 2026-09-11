"""Product identity must not be silently truncated into an incomplete scope.

`product_match_max` (default 3) was applied to the resolver's return value, so a
document naming more than three products had the surplus dropped — exact UINs
included — and `build_scope` then built the regulatory envelope from an
incomplete product set. Measured on the real corpus: 5 exact UINs -> 3 kept,
families {group, non_par, pension_annuity} lost; 10 -> 4 families and 4 of 9
rule scopes lost. The truncated scope still reports resolved=True, so
`scope_filter_values` applies a SQL cut that actively EXCLUDES the dropped
families' rules and precedents.

The limit itself is not free to remove wholesale: measured, it buys nothing on
the resolver (0.38-0.54 ms regardless of it) and nothing on the SQL filter
(bounded by the 8-category vocabulary at 76 values), but every grounded product
adds a ~650-token fact-card block to each analysis prompt. So the budget belongs
on prompt grounding — which is what config calls it, "max products grounded per
document" — and never on regulatory scope.
"""
from pathlib import Path

import pytest

from app.config import settings
from app.services.fact_card_service import FactCardService
from app.services.product_resolver import resolve_products
from app.services.rag.applicability import build_scope, validate_rules

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"
MIN_FUZZY = settings.kb_min_fuzzy_score

# Real, deliberately family-spanning products from the current corpus.
ULIP = "116L196V04"        # Bajaj Life Fortune Gain II       -> ulip
TERM = "116N165V01"        # Bajaj Life Saral Jeevan Bima     -> term
PAR = "116N127V04"         # Bajaj Life Elite Assure          -> par, savings_endowment
GROUP = "116G133V01"       # Bajaj Life Pradhan Mantri JJBY   -> group
PENSION = "116N169V16"     # Bajaj Life Saral Pension         -> non_par, pension_annuity
ULIP_2 = "116L204V01"      # Bajaj Life Goal Assure IV        -> ulip


@pytest.fixture(scope="module")
def cards():
    service = FactCardService(CARDS_DIR)
    assert not service.availability_issues, service.availability_issues
    return service


@pytest.fixture(scope="module")
def name(cards):
    return {p["uin"]: p["product_name"] for p in cards.all_products()}


def _resolve(text, cards):
    return resolve_products(text, cards, min_fuzzy_score=MIN_FUZZY)


def _portfolio(uins, name):
    return "Product Portfolio\n\n" + "\n\n".join(
        f"{name[u]}\nUIN: {u}" for u in uins
    )


def _uins(matches):
    return [m["uin"] for m in matches]


# --------------------------------------------------------------------------
# No silent loss.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("uins", [
    pytest.param([ULIP], id="1-product"),
    pytest.param([ULIP, TERM], id="2-products"),
    pytest.param([ULIP, TERM, PAR], id="3-products-unchanged"),
    pytest.param([ULIP, TERM, PAR, GROUP], id="4-products"),
    pytest.param([ULIP, TERM, PAR, GROUP, PENSION], id="5-products"),
])
def test_every_named_product_survives_resolution(uins, cards, name):
    resolved = _uins(_resolve(_portfolio(uins, name), cards))

    assert set(uins) <= set(resolved), f"dropped {set(uins) - set(resolved)}"


def test_ten_products_are_all_resolved(cards, name):
    uins = [p["uin"] for p in cards.all_products()][:10]
    resolved = _uins(_resolve(_portfolio(uins, name), cards))

    assert set(uins) <= set(resolved)


def test_five_exact_uins_all_survive(cards):
    """Exact identity is deterministic evidence; it must never be discarded."""
    text = "\n".join(f"UIN: {u}" for u in [ULIP, TERM, PAR, GROUP, PENSION])
    exact = [m["uin"] for m in _resolve(text, cards) if m["method"] == "uin_regex"]

    assert set(exact) == {ULIP, TERM, PAR, GROUP, PENSION}


def test_exact_uins_still_outrank_fuzzy_names(cards, name):
    """Precedence must stay: deterministic evidence first, in text order."""
    text = (f"Compare {name[ULIP]}, {name[PAR]} and {name[GROUP]}.\n\n"
            f"Also see UIN {TERM}.")
    resolved = _resolve(text, cards)

    assert resolved[0]["uin"] == TERM
    assert resolved[0]["method"] == "uin_regex"
    assert {m["uin"] for m in resolved} >= {ULIP, PAR, GROUP, TERM}


# --------------------------------------------------------------------------
# The scope those products imply.
# --------------------------------------------------------------------------


def test_scope_spans_every_detected_family(cards, name):
    uins = [ULIP, TERM, PAR, GROUP, PENSION]
    scope = build_scope(_resolve(_portfolio(uins, name), cards), cards)

    # Previously {par, savings_endowment, term, ulip} — group, non_par and
    # pension_annuity were dropped with the surplus products.
    assert scope.categories >= frozenset(
        {"ulip", "term", "par", "savings_endowment", "group", "non_par", "pension_annuity"}
    )


def test_same_family_products_deduplicate_into_one_scope(cards, name):
    scope = build_scope(_resolve(_portfolio([ULIP, ULIP_2], name), cards), cards)

    assert scope.categories == frozenset({"ulip"})


def test_truncation_no_longer_excludes_applicable_rules(cards, name):
    """The measured harm: a SQL/judge cut built from an incomplete family set."""
    uins = [ULIP, TERM, PAR, GROUP, PENSION]
    scope = build_scope(_resolve(_portfolio(uins, name), cards), cards)
    rules = [{"id": f"r-{c}", "product_line": c} for c in
             ["ulip", "term", "par", "non_par", "savings_endowment",
              "pension_annuity", "group", "rider", "global"]]
    accepted, _ = validate_rules(rules, scope, {r["id"]: r["product_line"] for r in rules})

    assert {r["id"] for r in accepted} >= {
        "r-ulip", "r-term", "r-par", "r-savings_endowment",
        "r-group", "r-non_par", "r-pension_annuity", "r-global",
    }


# --------------------------------------------------------------------------
# The prompt-grounding budget is still bounded, and its truncation is explicit.
# --------------------------------------------------------------------------


def test_grounding_budget_is_bounded_and_reported(cards, name):
    from app.services.agents.graph.nodes import _grounding_budget_signal

    matches = _resolve(_portfolio([ULIP, TERM, PAR, GROUP, PENSION], name), cards)
    signal = _grounding_budget_signal(matches, budget=3)

    assert signal["detected"] == len(matches)
    assert signal["grounded"] == 3
    assert signal["not_grounded"] == sorted(m["uin"] for m in matches[3:])
    assert len(signal["not_grounded"]) == len(matches) - 3


def test_no_grounding_signal_when_every_product_fits(cards, name):
    from app.services.agents.graph.nodes import _grounding_budget_signal

    matches = _resolve(_portfolio([ULIP, TERM], name), cards)

    assert _grounding_budget_signal(matches, budget=3) is None


def test_only_the_budgeted_products_are_injected_into_the_prompt(
    cards, name, monkeypatch
):
    """Scope is complete; the LLM prompt stays bounded. The two are separate."""
    import asyncio

    from app.services import fact_card_service as fcs
    from app.services.agents.graph import nodes as graph_nodes

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    monkeypatch.setattr(settings, "product_match_max", 3)
    monkeypatch.setattr(fcs, "get_fact_card_service", lambda: cards)

    matches = _resolve(_portfolio([ULIP, TERM, PAR, GROUP, PENSION], name), cards)
    assert len(matches) > 3, "premise: more products than the grounding budget"

    facts, _ = asyncio.run(
        graph_nodes._resolve_product_grounding(
            {"metadata": {"product_match": matches}}, []
        )
    )

    assert len(facts) <= 3
    assert [c["uin"] for c in facts] == [m["uin"] for m in matches[:3]]


# --------------------------------------------------------------------------
# Everything the earlier fixes established must still hold.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Bajaj Life",
    "Bajaj Life Insurance Limited",
    "Bajaj Life is committed to helping customers secure their future.",
])
def test_generic_corporate_text_still_resolves_to_no_product(text, cards):
    assert _resolve(text, cards) == []


def test_full_corpus_recall_is_unchanged(cards):
    misses = [
        p["uin"] for p in cards.all_products()
        if p["uin"] not in {m["uin"] for m in _resolve(p["product_name"], cards)}
    ]

    assert not misses


def test_global_filing_with_many_products_still_grades(cards, name):
    from app.services.agents.compliance.engine import ComplianceEngine
    from app.services.agents.graph.nodes import _submission_scope_signals

    text = _portfolio([ULIP, TERM, PAR, GROUP, PENSION], name)
    matches = _resolve(text, cards)

    assert _submission_scope_signals("global", matches, cards) == []
    assert ComplianceEngine.evaluate_persistability({
        "chunks": [{"id": "c-1", "text": text}],
        "status": "completed",
        "metadata": {"declared_product_line": "global", "product_match": matches},
    }) == (True, None)


def test_unknown_uin_among_many_products_is_reported_not_hidden(cards, name):
    """Five resolved products prove the envelope; the sixth, unknown one is a
    bounded evidence gap. It must be named on the run — never silently ignored
    (which would hide it) and never used to discard the other five's analysis
    (which is what refusing did). See
    tests/test_partial_analysis_unresolved_products.py."""
    from app.services.agents.compliance.engine import ComplianceEngine
    from app.services.product_resolver import unresolved_product_signals

    text = (_portfolio([ULIP, TERM, PAR, GROUP, PENSION], name)
            + "\n\nMystery plan (UIN: 116N999V01).")
    signals = unresolved_product_signals(text, cards)

    assert signals["unknown_uins"] == ["116N999V01"]
    state = {
        "chunks": [{"id": "c-1", "text": text}],
        "status": "completed",
        "metadata": {
            "product_unresolved": signals,
            "analysis_warnings": [
                {"code": "unknown_uins", "detail": {"uins": ["116N999V01"]}},
            ],
            "grounded_evidence": {"rules": 9, "precedents": 2, "product_facts": 3},
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)
    audit = ComplianceEngine.run_metadata_from_state(state)
    assert audit["analysis_warnings"][0]["detail"]["uins"] == ["116N999V01"]
    assert ComplianceEngine.cap_status_for_warnings(
        "passed", audit["analysis_warnings"]
    ) == "flagged"
