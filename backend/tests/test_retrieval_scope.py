"""Product-aware retrieval scope (RETRIEVAL_RCA.md, contract C1-C7).

Cases:
  A — a term creative must not receive ulip/pension-scoped rules; only
      explicitly global rules stay.
  B — a ULIP-linked rider (UIN 116A057V03) accepts rider- AND ulip-scoped
      items (structural flags widen scope) but not pension-scoped ones.
  C — semantically similar precedents from another product category are
      rejected on metadata, never on wording; unknown/NULL tags fail closed.
"""
from pathlib import Path

import pytest

from app.services.fact_card_service import FactCardService
from app.services.rag.applicability import (
    RetrievalScope,
    build_scope,
    normalize_category,
    validate_precedents,
    validate_rules,
)

CARDS_DIR = Path(__file__).resolve().parents[1] / "data" / "product_fact_cards"


# --- taxonomy normalisation (ingest hints ↔ fact-card categories) --------------

@pytest.mark.parametrize("raw,expected", [
    ("ULIP", "ulip"),
    ("ulip", "ulip"),
    ("Term", "term"),
    ("Savings", "savings_endowment"),
    ("savings_endowment", "savings_endowment"),
    ("Pension", "pension_annuity"),
    ("pension_annuity", "pension_annuity"),
    ("rider", "rider"),
    ("group", "group"),
])
def test_normalize_category_maps_both_taxonomies(raw, expected):
    assert normalize_category(raw) == expected


def test_normalize_category_unknown_is_none():
    assert normalize_category("Child") is None  # heuristic tag with no fact-card family
    assert normalize_category("") is None
    assert normalize_category(None) is None


# --- scope construction ---------------------------------------------------------

def _real_cards():
    return FactCardService(CARDS_DIR)


def test_scope_for_term_product():
    scope = build_scope([{"uin": "116N165V01"}], _real_cards())  # Saral Jeevan (term)
    assert scope.resolved
    assert "term" in scope.categories
    assert "ulip" not in scope.categories


def test_scope_for_ulip_linked_rider_widens_to_ulip():
    # Case B: 116A057V03 is product_category=rider with is_unit_linked=true.
    scope = build_scope([{"uin": "116A057V03"}], _real_cards())
    assert scope.resolved
    assert {"rider", "ulip"} <= scope.categories


def test_scope_unresolved_when_no_product():
    scope = build_scope([], _real_cards())
    assert not scope.resolved


def test_explicit_global_submission_scope_resolves_to_global_only():
    scope = build_scope([], _real_cards(), declared_product_line="global")

    assert scope.resolved
    assert scope.categories == frozenset()
    assert scope.declared_product_line == "global"


def test_declared_family_resolves_scope_without_exact_product_identity():
    scope = build_scope([], _real_cards(), declared_product_line=" ULIP ")

    assert scope.resolved
    assert scope.categories == frozenset({"ulip"})
    assert scope.declared_product_line == "ulip"


# --- Case A: rules validation ---------------------------------------------------

TERM_SCOPE = RetrievalScope(uins=frozenset({"116N165V01"}),
                            categories=frozenset({"term"}), resolved=True)

RULES = [
    {"id": "r-global", "rule_text": "Ads must not promise assured returns.", "rag_score": 0.9},
    {"id": "r-ulip", "rule_text": "ULIP ads must display the market-risk statement.", "rag_score": 0.8},
    {"id": "r-pension", "rule_text": "Pension products: tax on maturity must be disclosed.", "rag_score": 0.7},
]
PRODUCT_LINE = {
    "r-global": "global",
    "r-ulip": "ulip",
    "r-pension": "pension_annuity",
}


def test_term_scope_rejects_ulip_and_pension_rules():
    accepted, debug = validate_rules(RULES, TERM_SCOPE, PRODUCT_LINE)
    ids = {r["id"] for r in accepted}
    assert ids == {"r-global"}
    verdicts = {d["id"]: d["verdict"] for d in debug}
    assert verdicts["r-ulip"] == "rejected"
    assert verdicts["r-pension"] == "rejected"
    assert verdicts["r-global"] == "accepted"


def test_debug_records_carry_reason_and_scope_value():
    _, debug = validate_rules(RULES, TERM_SCOPE, PRODUCT_LINE)
    d = next(d for d in debug if d["id"] == "r-ulip")
    assert d["scope_value"] == "ulip"
    assert "conflict" in d["reason"]
    d_global = next(d for d in debug if d["id"] == "r-global")
    assert "global" in d_global["reason"]


def test_explicit_global_scope_is_distinct_from_missing_metadata():
    rules = [{"id": "r-explicit-global", "rule_text": "Applies to all products"}]
    accepted, debug = validate_rules(
        rules,
        TERM_SCOPE,
        {"r-explicit-global": "global"},
    )

    assert accepted == rules
    assert debug[0]["reason"].startswith("global_explicit")
    assert debug[0]["scope_value"] == "global"


def test_ulip_scope_keeps_ulip_rules():
    ulip_scope = RetrievalScope(uins=frozenset(), categories=frozenset({"ulip"}), resolved=True)
    accepted, _ = validate_rules(RULES, ulip_scope, PRODUCT_LINE)
    assert {r["id"] for r in accepted} == {"r-global", "r-ulip"}


def test_vice_versa_ulip_scope_rejects_term_rules():
    """Case A both directions: a non-term product must not receive term-scoped rules."""
    rules = RULES + [{"id": "r-term", "rule_text": "Term ads must state cover ceases at term end.", "rag_score": 0.6}]
    lines = {**PRODUCT_LINE, "r-term": "term"}
    ulip_scope = RetrievalScope(uins=frozenset(), categories=frozenset({"ulip"}), resolved=True)
    accepted, _ = validate_rules(rules, ulip_scope, lines)
    assert "r-term" not in {r["id"] for r in accepted}


def test_unresolved_scope_keeps_only_explicit_global_rules():
    scope = RetrievalScope(uins=frozenset(), categories=frozenset(), resolved=False)
    accepted, debug = validate_rules(RULES, scope, PRODUCT_LINE)
    assert {rule["id"] for rule in accepted} == {"r-global"}
    by_id = {row["id"]: row for row in debug}
    assert by_id["r-global"]["verdict"] == "accepted"
    assert "unresolved" in by_id["r-ulip"]["reason"]


def test_missing_and_unknown_scope_metadata_fail_closed():
    rules = [
        {"id": "missing", "rule_text": "missing scope"},
        {"id": "unknown", "rule_text": "unknown scope"},
    ]
    accepted, debug = validate_rules(
        rules,
        TERM_SCOPE,
        {"missing": None, "unknown": "mystery_product"},
    )

    assert accepted == []
    assert all(row["verdict"] == "rejected" for row in debug)
    assert all("scope_metadata_missing" in row["reason"] for row in debug)


# --- Case B: rider scope --------------------------------------------------------

def test_linked_rider_accepts_ulip_rules_but_not_pension():
    scope = build_scope([{"uin": "116A057V03"}], _real_cards())
    accepted, _ = validate_rules(RULES, scope, PRODUCT_LINE)
    ids = {r["id"] for r in accepted}
    assert "r-ulip" in ids       # linked rider → ulip rules apply
    assert "r-pension" not in ids


# --- Case C: precedents ---------------------------------------------------------

PRECEDENTS = [
    {"id": "p-ulip", "score": 0.95, "comment_text": "Add surrender charge table",
     "product_category": "ULIP"},          # ingest-taxonomy tag
    {"id": "p-term", "score": 0.90, "comment_text": "Substantiate claim ratio",
     "product_category": "Term"},
    {"id": "p-untagged", "score": 0.85, "comment_text": "Share UW approval",
     "product_category": None},
    {"id": "p-child", "score": 0.80, "comment_text": "Education goal wording",
     "product_category": "Child"},         # unmappable heuristic tag → global
]


def test_term_scope_rejects_ulip_and_untagged_precedents():
    accepted, debug = validate_precedents(PRECEDENTS, TERM_SCOPE)
    ids = {p["id"] for p in accepted}
    assert ids == {"p-term", "p-child"}
    v = {d["id"]: d for d in debug}
    assert v["p-ulip"]["verdict"] == "rejected"
    assert v["p-untagged"]["verdict"] == "rejected"
    assert "conflict" in v["p-ulip"]["reason"]
    assert "global_cross_cutting" in v["p-child"]["reason"]


def test_similarity_score_cannot_override_category_conflict():
    """Case C core: highest-scoring hit still rejected on metadata."""
    accepted, _ = validate_precedents(PRECEDENTS, TERM_SCOPE)
    top = max(PRECEDENTS, key=lambda p: p["score"])
    assert top["id"] == "p-ulip"
    assert top["id"] not in {p["id"] for p in accepted}


# --- retriever mapper must surface the metadata (was dropped) -------------------

def test_precedent_hit_mapper_carries_product_category():
    from app.services.rag.ports import SearchHit
    from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent
    hit = SearchHit(id="x", score=0.9, fields={
        "reviewer_comment": "c", "span_context": "s", "product_category": "ULIP",
    })
    assert _hit_to_precedent(hit)["product_category"] == "ULIP"
