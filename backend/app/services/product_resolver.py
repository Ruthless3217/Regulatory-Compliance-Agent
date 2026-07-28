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

_UIN_RE = re.compile(r"\b\d{3}[A-Z]\d{3}V\d{2}\b")


def resolve_products(
    text: str,
    fact_card_service,
    *,
    max_matches: int,
    min_fuzzy_score: int,
) -> List[Dict[str, Any]]:
    text = text or ""
    products = fact_card_service.all_products()
    known_uins = {p["uin"] for p in products}
    name_by_uin = {p["uin"]: (p.get("product_name") or "") for p in products}
    # Cards per UIN: >1 means product/variant records share the UIN (e.g.
    # 116L214V01 ×3). A match on such a UIN is AMBIGUOUS — surface every
    # candidate name instead of silently grading against one variant.
    candidates_by_uin: Dict[str, List[str]] = {}
    for p in products:
        candidates_by_uin.setdefault(p["uin"], []).append(p.get("product_name") or "")

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
        uin = m.group(0)
        if uin in known_uins and uin not in seen_uins:
            seen_uins.add(uin)
            matches.append(_entry(uin, name_by_uin.get(uin, ""), 1.0, "uin_regex"))

    # 2. Fuzzy product-name match for products not already matched by UIN.
    # One UIN emits ONE entry (the best-scoring variant name) — variant cards
    # sharing a UIN must not each consume a slot of the max_matches budget.
    lowered = text.lower()
    best_fuzzy: Dict[str, Dict[str, Any]] = {}
    for p in products:
        if p["uin"] in seen_uins:
            continue
        name = (p.get("product_name") or "").strip()
        if not name:
            continue
        score = fuzz.partial_ratio(name.lower(), lowered)
        if score >= min_fuzzy_score:
            entry = _entry(p["uin"], name, round(score / 100.0, 3), "name_fuzzy")
            prev = best_fuzzy.get(p["uin"])
            if prev is None or entry["confidence"] > prev["confidence"]:
                best_fuzzy[p["uin"]] = entry
    fuzzy = sorted(best_fuzzy.values(), key=lambda x: x["confidence"], reverse=True)

    ranked = matches + fuzzy
    if len(ranked) > max_matches:
        logger.info(
            "product_resolver: %d products matched; capping to %d (dropped %s)",
            len(ranked), max_matches, [m["uin"] for m in ranked[max_matches:]],
        )
    return ranked[:max_matches]
