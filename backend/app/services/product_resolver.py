"""Resolve which approved product(s) a submission is about.

UIN regex first (exact, high confidence), then fuzzy product-name match against
the known fact-card names. Returns up to ``max_matches`` matches; an empty list
means "no confident product" — downstream product grounding then no-ops, leaving
precedent/rule/novel grading unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_UIN_RE = re.compile(r"\b\d{3}[A-Z]\d{3}V\d{2}\b", re.IGNORECASE)


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
    max_matches: int,
    min_fuzzy_score: int,
) -> List[Dict[str, Any]]:
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
        if name:
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
            score = fuzz.partial_ratio(alias.lower(), lowered)
            if score >= min_fuzzy_score:
                _consider(p["uin"], name or alias, round(score / 100.0, 3), "alias_match")
    fuzzy = sorted(best_fuzzy.values(), key=lambda x: x["confidence"], reverse=True)

    ranked = matches + fuzzy
    if len(ranked) > max_matches:
        logger.info(
            "product_resolver: %d products matched; capping to %d (dropped %s)",
            len(ranked), max_matches, [m["uin"] for m in ranked[max_matches:]],
        )
    return ranked[:max_matches]
