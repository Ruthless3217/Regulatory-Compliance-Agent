"""Resolve which approved product(s) a submission is about.

UIN regex first (exact, high confidence), then fuzzy product-name match against
the known fact-card names. Returns EVERY confidently identified product; an
empty list means "no confident product" — downstream product grounding then
no-ops, leaving precedent/rule/novel grading unchanged.

The result is the document's product IDENTITY, and `build_scope` turns it into
the regulatory envelope, so it must be complete: capping it silently dropped
whole product families out of applicability. Bounding what the LLM prompt can
carry is a separate concern with a separate budget (settings.product_match_max,
applied at grounding in graph/nodes.py), because that is the only thing that
actually scales — measured, the cap changes neither resolver time nor the SQL
filter, which is bounded by the category vocabulary.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, FrozenSet, List, Optional, Set

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_UIN_RE = re.compile(r"\b\d{3}[A-Z]\d{3}V\d{2}\b", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z0-9]+")

# Version/variant markers (eTouch II, Goal Assure IV, Fortune Gain 2). They
# distinguish editions of one product, not one product from another, so a
# document naming "eTouch" without the edition still names eTouch.
_VERSION_TOKEN_RE = re.compile(r"^(?:i{1,3}v?|iv|vi{0,3}|vii|v|\d{1,2})$", re.IGNORECASE)

# A token this common across product names is the BRAND, not a product: on the
# real corpus `bajaj` and `life` each occur in 100% of the 44 names and nothing
# else exceeds 25%. Measured across 30%-90% the derived set is identical, so the
# cutoff sits inside a wide gap rather than on a tuned edge. It is derived from
# the loaded corpus, never hardcoded, so a rebrand re-derives it.
_BRAND_TOKEN_MIN_SHARE = 0.9

# How far apart a product's identity tokens may sit and still count as NAMING it.
# Measured on the real corpus and a real 25KB document: the longest legitimate
# product name needs a span of 7, a genuinely named product spans 2, and the
# nearest accidental co-occurrence of an unnamed product's tokens spans 18. The
# band [8, 15] gives 44/44 recall and zero false matches; 18 breaks it. This is
# the middle of that band, not an edge of it.
_IDENTITY_WINDOW_TOKENS = 12


def _tokens(value: str) -> List[str]:
    return _WORD_RE.findall((value or "").lower())


def _brand_tokens(names: List[str]) -> FrozenSet[str]:
    """Tokens carried by (nearly) every product name — zero identifying power."""
    total = len(names)
    if not total:
        return frozenset()
    frequency: Counter = Counter()
    for name in names:
        for token in set(_tokens(name)):
            frequency[token] += 1
    return frozenset(
        token for token, count in frequency.items()
        if count / total >= _BRAND_TOKEN_MIN_SHARE
    )


def _identity_tokens(name: str, brand: FrozenSet[str]) -> Set[str]:
    """The tokens that name THIS product rather than the brand.

    Falls back to every non-version token when stripping the brand would leave
    nothing: a product whose name is brand-only cannot be identified by name,
    and demanding its full name is the fail-closed reading.
    """
    identity = {
        token for token in _tokens(name)
        if token not in brand and not _VERSION_TOKEN_RE.match(token)
    }
    return identity or {
        token for token in _tokens(name) if not _VERSION_TOKEN_RE.match(token)
    }


def _token_positions(text: str) -> Dict[str, List[int]]:
    """token -> every position it occupies, built once per document."""
    positions: Dict[str, List[int]] = {}
    for index, token in enumerate(_tokens(text)):
        positions.setdefault(token, []).append(index)
    return positions


def _min_identity_window(
    positions: Dict[str, List[int]], identity: Set[str]
) -> Optional[int]:
    """Smallest token span containing every identity token, None if any is absent.

    Standard minimum-window sweep over the merged occurrence lists of just this
    product's tokens, so the cost is proportional to how often those few words
    occur rather than to the document length.
    """
    occurrences = sorted(
        (index, token)
        for token in identity
        for index in positions.get(token, ())
    )
    if len({token for _, token in occurrences}) != len(identity):
        return None
    counts: Dict[str, int] = {}
    distinct = 0
    best: Optional[int] = None
    low = 0
    for high, (_, token) in enumerate(occurrences):
        counts[token] = counts.get(token, 0) + 1
        if counts[token] == 1:
            distinct += 1
        while distinct == len(identity):
            span = occurrences[high][0] - occurrences[low][0]
            best = span if best is None else min(best, span)
            leaving = occurrences[low][1]
            counts[leaving] -= 1
            if counts[leaving] == 0:
                distinct -= 1
            low += 1
    return best


def _document_names(
    positions: Dict[str, List[int]], name: str, brand: FrozenSet[str]
) -> bool:
    """True when the document NAMES this product — locally, not by scattering.

    This is the gate `fuzz.partial_ratio` cannot provide. partial_ratio slides
    the shorter string over the longer one, so the bare brand is an EXACT
    substring of every product name and scores 100 — the same score a full-name
    match gets. Twelve generic corporate inputs each resolved to three products
    at the production threshold. No metric swap fixes it: token_set_ratio and
    WRatio score "Bajaj Life" and "Smart Secure ROP" identically (100/100 and
    90/90), and `ratio` inverts on long documents.

    Requiring the identity tokens to be PRESENT was still not enough. Presence is
    a document-wide test, and a real 25KB document is dense in the words product
    names are built from — `secure` x52, `term` x51, `smart` x44 in the one that
    exposed this. Seven products the document never names satisfied it, one of
    them a three-variant collision UIN, so a fabricated match also fabricated an
    ambiguity and failed the run closed.

    So the evidence must be LOCAL: every identity token inside one window. On
    that document a genuinely named product spans 2 tokens and the nearest
    accidental co-occurrence spans 18, while the longest legitimate name in the
    corpus needs 7 — a real gap, not a tuned edge. The window band [8, 15] holds
    44/44 recall with zero false matches and breaks at 18; this sits mid-band.
    """
    identity = _identity_tokens(name, brand)
    if not identity:
        return False
    span = _min_identity_window(positions, identity)
    return span is not None and span <= _IDENTITY_WINDOW_TOKENS


def unresolved_product_signals(
    text: str,
    fact_card_service,
) -> Dict[str, List[str]]:
    """Find product identifiers/names that the fact-card corpus cannot ground."""
    text = text or ""
    products = fact_card_service.all_products()
    known_uins = {
        str(uin).upper()
        for uin in (
            set(fact_card_service.known_uins)
            if hasattr(fact_card_service, "known_uins")
            else {product["uin"] for product in products}
        )
    }
    extracted_uins = {match.group(0).upper() for match in _UIN_RE.finditer(text)}
    unknown_uins = sorted(extracted_uins - known_uins)
    rider_uins_without_fact_cards = sorted(
        extracted_uins
        & {
            str(uin).upper()
            for uin in getattr(
                fact_card_service, "rider_uins_without_fact_cards", set()
            )
        }
    )

    lowered = re.sub(r"\s+", " ", text).lower()
    missing_names: List[str] = []
    for product in getattr(fact_card_service, "declared_without_fact_cards", []):
        names = [product.get("name") or ""] + list(product.get("aliases") or [])
        normalized_names = [
            re.sub(r"\s+", " ", name).strip().lower()
            for name in names if name
        ]
        if any(
            re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered)
            for name in normalized_names
        ):
            missing_names.append(product.get("name") or "")

    return {
        "unknown_uins": unknown_uins,
        "rider_uins_without_fact_cards": rider_uins_without_fact_cards,
        "declared_products_without_fact_cards": sorted(set(missing_names)),
    }


def resolve_products(
    text: str,
    fact_card_service,
    *,
    min_fuzzy_score: int,
    max_matches: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Every product this document confidently identifies, exact matches first.

    `max_matches` is an optional hard cap for callers that genuinely want a
    shortlist. It is NOT the grounding budget and no longer defaults to one:
    truncating here silently narrowed the regulatory scope.
    """
    text = text or ""
    products = fact_card_service.all_products()
    known_uins = {str(p["uin"]).upper() for p in products}
    name_by_uin = {
        str(p["uin"]).upper(): (p.get("product_name") or "")
        for p in products
    }
    # Cards per UIN: >1 means product/variant records share the UIN (e.g.
    # 116L214V01 ×3). A match on such a UIN is AMBIGUOUS — surface every
    # candidate name instead of silently grading against one variant.
    candidates_by_uin: Dict[str, List[str]] = {}
    for p in products:
        candidates_by_uin.setdefault(
            str(p["uin"]).upper(), []
        ).append(p.get("product_name") or "")

    def _entry(uin: str, name: str, confidence: float, method: str) -> Dict[str, Any]:
        cands = candidates_by_uin.get(uin, [])
        return {
            "uin": uin,
            "product_name": name,
            "confidence": confidence,
            "method": method,
            "ambiguous": len(cands) > 1,
            "candidates": list(cands) if len(cands) > 1 else [],
        }

    matches: List[Dict[str, Any]] = []
    seen_uins: set = set()

    # 1. UIN regex — exact, ranked first. Preserve first-seen order in the text.
    for m in _UIN_RE.finditer(text):
        uin = m.group(0).upper()
        if uin in known_uins and uin not in seen_uins:
            seen_uins.add(uin)
            matches.append(_entry(uin, name_by_uin.get(uin, ""), 1.0, "uin_regex"))

    # 2. Fuzzy product-name match for products not already matched by UIN.
    # One UIN emits ONE entry (the best-scoring variant name) — variant cards
    # sharing a UIN must not each consume a slot of the max_matches budget.
    # Marketing/feature aliases (fact-card `marketing_aliases`) also resolve:
    # feature collateral like an HMS services table carries no product name, so
    # the alias is its only grounding. Short aliases (acronyms like "HMS") are
    # matched on word boundaries only — partial_ratio on a 3-letter needle
    # fires on noise like "months".
    lowered = text.lower()
    # A fuzzy score alone cannot establish product identity here (see
    # _document_names): every name shares the brand prefix, and a long document
    # scatters the words names are built from, so the document must be shown to
    # name the product LOCALLY before its score is allowed to mean anything.
    brand = _brand_tokens([p.get("product_name") or "" for p in products])
    text_positions = _token_positions(text)
    best_fuzzy: Dict[str, Dict[str, Any]] = {}

    def _consider(uin: str, display_name: str, confidence: float, method: str) -> None:
        entry = _entry(uin, display_name, confidence, method)
        prev = best_fuzzy.get(uin)
        if prev is None or entry["confidence"] > prev["confidence"]:
            best_fuzzy[uin] = entry

    for p in products:
        if p["uin"] in seen_uins:
            continue
        name = (p.get("product_name") or "").strip()
        if name and _document_names(text_positions, name, brand):
            score = fuzz.partial_ratio(name.lower(), lowered)
            if score >= min_fuzzy_score:
                _consider(p["uin"], name, round(score / 100.0, 3), "name_fuzzy")
        for alias in (p.get("aliases") or []):
            alias = (alias or "").strip()
            if not alias:
                continue
            if len(alias) < 8:
                if re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
                    _consider(p["uin"], name or alias, 0.9, "alias_match")
                continue
            if not _document_names(text_positions, alias, brand):
                continue
            score = fuzz.partial_ratio(alias.lower(), lowered)
            if score >= min_fuzzy_score:
                _consider(p["uin"], name or alias, round(score / 100.0, 3), "alias_match")
    fuzzy = sorted(best_fuzzy.values(), key=lambda x: x["confidence"], reverse=True)

    ranked = matches + fuzzy
    if max_matches is not None and len(ranked) > max_matches:
        logger.info(
            "product_resolver: %d products matched; caller capped to %d (dropped %s)",
            len(ranked), max_matches, [m["uin"] for m in ranked[max_matches:]],
        )
        return ranked[:max_matches]
    return ranked
