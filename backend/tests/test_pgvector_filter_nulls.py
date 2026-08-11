"""_build_filter_clause NULL semantics — the groundwork for pushing product
scope into SQL instead of filtering retrieved candidates in Python.

Why this is load-bearing: SQL IN never matches NULL, so a naive scoped filter
would exclude every untagged precedent before the deterministic applicability
layer can audit and reject it. Retaining NULL candidates makes missing metadata
observable; it does not make those candidates globally applicable.
"""
import pytest

from app.services.rag.stores.pgvector_store import _build_filter_clause


def _clause(filters, index="precedent_cases"):
    params: dict = {}
    return _build_filter_clause(index, filters, params), params


# --- the contract this exists for --------------------------------------------

def test_none_in_list_keeps_untagged_candidates_for_applicability_audit():
    clause, params = _clause({"product_category": ["ulip", "par", None]})
    assert "IS NULL" in clause, "untagged rows would be silently excluded"
    assert "OR" in clause
    assert sorted(params.values()) == ["par", "ulip"]
    assert None not in params.values(), "NULL must not be bound as a parameter"


def test_list_without_none_stays_strict():
    clause, params = _clause({"product_category": ["ulip"]})
    assert "IS NULL" not in clause
    assert "IN (" in clause
    assert list(params.values()) == ["ulip"]


def test_only_none_means_untagged_only():
    clause, params = _clause({"product_category": [None]})
    assert "IS NULL" in clause
    assert "IN (" not in clause
    assert params == {}


def test_scalar_none_emits_is_null_not_equals_null():
    # `field = NULL` is never true for any row in SQL — a scalar None filter
    # used to silently match zero rows instead of matching untagged ones.
    clause, params = _clause({"product_category": None})
    assert "IS NULL" in clause
    assert "= :" not in clause
    assert params == {}


# --- unchanged existing behaviour --------------------------------------------

def test_empty_collection_still_matches_nothing():
    clause, _ = _clause({"product_category": []})
    assert "FALSE" in clause


def test_scalar_value_unchanged():
    clause, params = _clause({"severity": "critical"})
    assert "severity = :f_severity" in clause
    assert params["f_severity"] == "critical"


def test_no_filters_returns_empty_string():
    clause, params = _clause(None)
    assert clause == ""
    assert params == {}


def test_multiple_filters_are_anded():
    clause, _ = _clause({"severity": "high", "product_category": ["ulip", None]})
    assert clause.startswith(" AND ")
    assert clause.count("AND") >= 2


# --- the whitelist is still a real guard --------------------------------------

def test_product_category_is_allowed_on_precedents():
    clause, _ = _clause({"product_category": ["term"]})
    assert "product_category" in clause


def test_product_line_is_allowed_on_rag_rules():
    # Migration 0036 added product_line to rag_rules, so the pushdown filter is
    # now a real column reference (it used to be rejected as nonexistent).
    clause, params = _clause({"product_line": ["term"]}, index="rag_rules")
    assert "product_line IN (" in clause
    assert list(params.values()) == ["term"]


def test_unknown_field_still_rejected():
    with pytest.raises(ValueError, match="not allowed"):
        _clause({"drop_table": "x"})
