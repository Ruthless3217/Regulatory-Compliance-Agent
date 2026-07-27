"""Acceptable insurance-domain language allowlist (Workstream B, 2026-07-15).

Legitimate generic product terms ("term insurance", "ULIP") and neutral
death-scenario phrasing ("passes away", "sudden death") were being raised as
brand-naming / tone false positives. This module loads a curated allowlist and
exposes a whole-span membership test used by:

  1. the grading prompt (an "ACCEPTABLE LANGUAGE — never flag these" block), and
  2. a post-filter backstop that suppresses a brand/tone finding whose entire
     offending span is exactly an allowlisted phrase.

Load-bearing safety property: matching is WHOLE-SPAN and exact (after
lowercase + whitespace normalization). A phrase that merely appears inside a
longer non-compliant claim is NOT allowlisted, so real findings are preserved.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

import yaml

logger = logging.getLogger(__name__)

# backend/app/services/compliance_allowlist.py -> parents[2] == backend/
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "compliance_allowlist.yaml"


def _normalize(s: str) -> str:
    """Lowercase + collapse every whitespace run to a single space.

    Mirrors ``graph.nodes._normalize_ws`` so the allowlist, the prompt, and the
    evidence-grounding check all compare spans the same way.
    """
    return " ".join((s or "").lower().split())


@functools.lru_cache(maxsize=4)
def load_allowlist(path: Optional[str] = None) -> Dict[str, List[str]]:
    """Load the allowlist YAML. Cached per path. Missing/empty file → empty
    sections (fail-soft: the allowlist simply exempts nothing)."""
    p = Path(path) if path else _DEFAULT_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("compliance allowlist not found at %s; exempting nothing", p)
        data = {}
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("failed to load compliance allowlist (%s); exempting nothing", e)
        data = {}
    return {
        "generic_product_terms": list(data.get("generic_product_terms") or []),
        "acceptable_scenario_phrases": list(data.get("acceptable_scenario_phrases") or []),
    }


@functools.lru_cache(maxsize=4)
def allowlisted_phrases(path: Optional[str] = None) -> FrozenSet[str]:
    """Normalized union of every allowlisted term/phrase."""
    al = load_allowlist(path)
    merged = al["generic_product_terms"] + al["acceptable_scenario_phrases"]
    return frozenset(n for n in (_normalize(t) for t in merged) if n)


def is_allowlisted_span(text: str, phrases: Optional[FrozenSet[str]] = None) -> bool:
    """True iff the WHOLE normalized span equals an allowlisted phrase.

    ``phrases`` may be supplied for pure testing; otherwise the loaded file set
    is used. Substrings never match — that is the safety property that keeps a
    real claim containing an allowlisted term flaggable.
    """
    key = _normalize(text)
    if not key:
        return False
    ph = phrases if phrases is not None else allowlisted_phrases()
    return key in ph


@functools.lru_cache(maxsize=4)
def allowlist_prompt_block(path: Optional[str] = None) -> str:
    """The ACCEPTABLE LANGUAGE carve-out injected into the grading prompt.

    Built from the same file the post-filter reads, so the prompt and the
    backstop never drift. Covers every tier — important for the novel tier,
    where the post-filter cannot gate on a rule identity.
    """
    al = load_allowlist(path)
    terms = ", ".join(al["generic_product_terms"]) or "(none)"
    phrases = ", ".join(al["acceptable_scenario_phrases"]) or "(none)"
    return (
        "ACCEPTABLE LANGUAGE — the following are legitimate, standard insurance "
        "terms and neutral descriptions of the insured event. NEVER flag any of "
        "these as a brand-naming issue or a tone/fear issue, and NEVER suggest "
        "replacing a generic product category with the company brand name "
        "('Bajaj Life Insurance'):\n"
        f"  Generic product categories: {terms}\n"
        f"  Neutral death-scenario phrasing: {phrases}\n"
        "Only flag such a phrase if the surrounding sentence is non-compliant for "
        "some OTHER reason (e.g. an unsubstantiated guarantee, a missing "
        "disclaimer) — never for the term/phrase alone."
    )
