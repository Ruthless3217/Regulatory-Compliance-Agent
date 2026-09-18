"""What kind of limitation a run warning describes.

A warning on `analysis_runs.run_metadata.analysis_warnings` says WHAT was
limited. Three very different things end up in that list, and the reviewer
must be told which one they are looking at:

    coverage        part of THIS document could not be product/identity
                    grounded — a rider with no fact card, an unknown UIN, a
                    name the corpus has no edition for, a product the prompt
                    budget could not carry. The score genuinely covers less
                    than the whole document.

    tier            an evidence TIER is limited by the knowledge base, not by
                    the document — the precedent corpus is empty, or the
                    document's candidate rules/precedents carried no product
                    scope and were refused. Every section was analysed; one
                    source of evidence was thinner than it should be.

    infrastructure  a retrieval component FAILED (an embedding-model
                    mismatch, a store error). Every section was analysed; the
                    evidence that retrieval would have supplied never arrived.

Before this distinction existed, the provenance audit of 2026-09-17 found
three codes firing on 9 of 9 stored documents — two of them in a
retrievers-healthy mode as well — and every one of them made the banner claim
"the score covers less than the whole document". That claim is true only for
the coverage kind.

Persisted warnings written before this module carry no `kind`; they are
classified here by code. A code this build has never seen is treated as
coverage — the most restrictive reading — never a lighter one.

This module is deliberately dependency-free so the API, the export layer and
the approval gate can share it without importing the graph.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

COVERAGE = "coverage"
TIER = "tier"
INFRASTRUCTURE = "infrastructure"

KIND_BY_CODE: Dict[str, str] = {
    # coverage — the document itself could not be fully grounded
    "rider_uins_without_fact_cards": COVERAGE,
    "unknown_uins": COVERAGE,
    "declared_products_without_fact_cards": COVERAGE,
    "edition_conflicts": COVERAGE,
    "product_grounding_budget": COVERAGE,
    # tier — the knowledge base limited an evidence tier
    "precedent_corpus_empty": TIER,
    "precedent_scope_metadata_incomplete": TIER,
    "rule_scope_metadata_incomplete": TIER,
    # legacy single precedent code (PR #20 runs): it could mean any of the
    # precedent states, so it is read as the tier limitation it usually was
    "precedent_evidence_unavailable": TIER,
    # infrastructure — a retrieval component failed
    "retrieval_degraded": INFRASTRUCTURE,
    "precedent_tier_unavailable": INFRASTRUCTURE,
}


def warning_kind(warning: Dict[str, Any]) -> str:
    """The kind of one warning: its stored `kind`, else its code, else coverage."""
    stored = (warning or {}).get("kind")
    if stored in (COVERAGE, TIER, INFRASTRUCTURE):
        return stored
    return KIND_BY_CODE.get(str((warning or {}).get("code") or ""), COVERAGE)


def warning_kinds(warnings: Iterable[Dict[str, Any]]) -> Set[str]:
    return {warning_kind(w) for w in (warnings or []) if isinstance(w, dict) and w.get("code")}


def evidence_coverage_state(warnings: Iterable[Dict[str, Any]]) -> str:
    """'partial' when any coverage warning exists, else 'complete'.

    This is the ONLY thing that may justify telling a reviewer the score
    covers less than the whole document.
    """
    return "partial" if COVERAGE in warning_kinds(warnings) else "complete"


def limitation_statement(warnings: Iterable[Dict[str, Any]]) -> str:
    """The one-sentence claim the banner, the export and the approval blocker
    may make about a warned run. Coverage wins when present, then
    infrastructure, then tier; empty when there is nothing to say."""
    kinds = warning_kinds(warnings)
    if not kinds:
        return ""
    if COVERAGE in kinds:
        return ("This document was graded on incomplete evidence — the score "
                "covers less than the whole document.")
    if INFRASTRUCTURE in kinds:
        return ("This document was graded while a retrieval component failed — "
                "every section was analysed, but evidence that retrieval would "
                "have supplied was unavailable.")
    return ("This document was graded with limited evidence sources — every "
            "section was analysed, but some knowledge-base evidence was "
            "unavailable.")


def classified(warnings: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of the warnings with `kind` filled in — for API payloads."""
    return [
        {**w, "kind": warning_kind(w)}
        for w in (warnings or []) if isinstance(w, dict) and w.get("code")
    ]
