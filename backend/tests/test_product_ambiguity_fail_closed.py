"""Ambiguous product identities must never select an arbitrary fact card."""

import asyncio

import pytest
from fastapi import HTTPException

from app.api.routes import submissions as submission_routes
from app.services.agents.compliance.engine import ComplianceEngine
from app.services.agents.graph.nodes import (
    _ambiguous_product_uins,
    _resolve_product_grounding,
    _submission_scope_signals,
)


AMBIGUOUS_MATCH = {
    "uin": "116L214V01",
    "product_name": "Smart Wealth Goal VI",
    "ambiguous": True,
    "candidates": ["Child Wealth", "Joint Life Wealth", "Wealth"],
}


class _UlipCards:
    def get_all(self, _uin):
        return [{
            "product_category": "ulip",
            "structural_flags": {"is_unit_linked": True},
        }]


def test_ambiguous_uins_are_stable_and_deduplicated():
    matches = [
        AMBIGUOUS_MATCH,
        {**AMBIGUOUS_MATCH, "product_name": "another variant"},
        {"uin": "116N208V03", "ambiguous": False},
    ]

    assert _ambiguous_product_uins(matches) == ["116L214V01"]


def test_missing_resolved_and_declared_product_scope_fails_closed():
    issues = _submission_scope_signals(None, [], None)

    assert issues
    assert "no product was resolved" in issues[0]


def test_explicit_global_scope_is_allowed_with_or_without_a_detected_product():
    """Global is the universal scope; a detected product narrows it, safely.

    This previously refused the combination, which discarded completed analyses
    of multi-product collateral as `product_unresolved` while every UIN was in
    fact fully grounded. See tests/test_global_scope_not_a_conflict.py for the
    reproduction and the superset argument.
    """
    assert _submission_scope_signals("global", [], None) == []
    assert _submission_scope_signals(
        "global", [{"uin": "116L999V01"}], _UlipCards()
    ) == []


def test_declared_family_must_agree_with_detected_product():
    match = [{"uin": "116L999V01"}]

    assert _submission_scope_signals("ulip", match, _UlipCards()) == []
    assert "conflicts with detected scope" in _submission_scope_signals(
        "term", match, _UlipCards()
    )[0]


@pytest.mark.parametrize("scope", [None, "", "unknown"])
def test_submission_api_requires_supported_product_scope(scope):
    with pytest.raises(HTTPException) as exc:
        submission_routes._validated_product_line(scope)

    assert getattr(exc.value, "status_code", None) == 400


def test_submission_api_normalizes_supported_product_scope():
    assert submission_routes._validated_product_line(" ULIP ") == "ulip"


def test_ambiguous_product_blocks_persistence_as_needs_review():
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {
            "degraded": "product_ambiguous",
            "product_match": [AMBIGUOUS_MATCH],
            "product_ambiguous_uins": ["116L214V01"],
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (
        False,
        "product_ambiguous",
    )
    assert "product_ambiguous" in ComplianceEngine._NEEDS_REVIEW_REASONS


def test_ambiguous_product_never_reaches_fact_card_lookup(monkeypatch):
    # The early return is the safety property: no compatibility get() or
    # last-file-wins lookup may feed one variant into the grading prompt.
    from app.config import settings

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    state = {"metadata": {"product_match": [AMBIGUOUS_MATCH]}}

    assert asyncio.run(_resolve_product_grounding(state, [])) == ([], {})


def test_ambiguity_metadata_is_retained_in_run_audit():
    state = {
        "metadata": {
            "degraded": "product_ambiguous",
            "product_match": [AMBIGUOUS_MATCH],
            "product_ambiguous_uins": ["116L214V01"],
        }
    }

    audit = ComplianceEngine.run_metadata_from_state(state)
    assert audit["product_ambiguous_uins"] == ["116L214V01"]
    assert audit["product_match"][0]["candidates"] == AMBIGUOUS_MATCH["candidates"]


def test_unprovable_product_scope_blocks_grade_and_is_a_review_reason():
    """`product_unresolved` now means exactly one thing: the regulatory scope
    could not be established. A missing fact card for a named product is an
    evidence gap and is reported as a warning instead
    (tests/test_partial_analysis_unresolved_products.py)."""
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {
            "product_unresolved": {
                "submission_scope": [
                    "no product was resolved and no supported product_line "
                    "was declared"
                ],
                "unknown_uins": ["116N999V01"],
            },
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (
        False,
        "product_unresolved",
    )
    assert "product_unresolved" in ComplianceEngine._NEEDS_REVIEW_REASONS


def test_product_safety_signal_cannot_be_masked_by_another_degraded_reason():
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {
            "degraded": "rag_degraded",
            "product_ambiguous_uins": ["116L214V01"],
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (
        False,
        "product_ambiguous",
    )


def test_resolution_failure_is_a_needs_review_reason():
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {
            "product_resolution_failed": "RuntimeError",
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (
        False,
        "product_resolution_failed",
    )
    assert "product_resolution_failed" in ComplianceEngine._NEEDS_REVIEW_REASONS


def test_scope_metadata_gap_is_reported_instead_of_dropping_rules_silently():
    """The applicability judge still rejects every untagged rule, per item —
    nothing is ever admitted on a guess. What changed is that the rejection is
    NAMED on the run rather than aborting it: an untagged rule is a recall loss
    with a known cause, and the floor below still refuses if it leaves the run
    with no evidence at all."""
    metadata = {
        "scope_metadata_missing": {
            "count": 1,
            "examples": [{"corpus": "rules", "id": "r-1"}],
        },
        "analysis_warnings": [
            {"code": "rule_scope_metadata_incomplete", "detail": {"count": 1}},
        ],
    }
    state = {
        "chunks": [{"id": "chunk-1", "text": "creative"}],
        "status": "completed",
        "metadata": {
            **metadata,
            "grounded_evidence": {"rules": 8, "precedents": 3, "product_facts": 1},
        },
    }

    assert ComplianceEngine.evaluate_persistability(state) == (True, None)
    assert ComplianceEngine.run_metadata_from_state(state)["analysis_warnings"]

    starved = {
        **state,
        "metadata": {
            **metadata,
            "grounded_evidence": {"rules": 0, "precedents": 0, "product_facts": 0},
        },
    }

    assert ComplianceEngine.evaluate_persistability(starved) == (
        False,
        "no_grounded_evidence",
    )


def test_unprovable_scope_skips_grounding_even_if_degraded_slot_differs(monkeypatch):
    """The `degraded` slot is last-writer-wins, so it must not be what decides
    whether grounding is safe. An evidence gap no longer skips grounding at all
    — withholding the resolved products' own cards made the analysis worse and
    then refused it (test_partial_analysis_unresolved_products.py)."""
    from app.config import settings

    monkeypatch.setattr(settings, "product_grounding_enabled", True)
    state = {
        "metadata": {
            "degraded": "rag_degraded",
            "product_match": [{"uin": "116N208V03"}],
            "product_unresolved": {
                "submission_scope": ["unsupported declared product_line: bogus"],
                "unknown_uins": ["116N999V01"],
            },
        },
    }

    assert asyncio.run(_resolve_product_grounding(state, [])) == ([], {})
