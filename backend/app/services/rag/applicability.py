"""Deterministic regulatory-applicability layer for retrieval (RETRIEVAL_RCA.md).

Product identity is resolved BEFORE retrieval (librarian node), and the corpora
already carry scope metadata (`Rule.product_line`,
`precedent_cases.product_category`) — this module is the missing consumer. It
builds a RetrievalScope from the resolved products and validates every
retrieved candidate against it, so regulatory applicability is decided
deterministically before semantic similarity is allowed to matter.

Contract (RETRIEVAL_RCA.md §5): a category CONFLICT rejects (C1); untagged or
unmappable tags are GLOBAL and accepted with a label (C2/C7); an unresolved
scope rejects nothing but labels everything (C3); a unit-linked rider widens
its scope to ulip (C4). Every decision is returned as a debug record so the
retrieval debugger can show why each chunk entered or was refused (C6).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical product families = the fact-card `product_category` enum, plus the
# business MARKET SEGMENTS (Par / Term / Non-Par / ULIP — declared segregation
# in backend/data/product_segments.json, 2026-07-28). Segments and categories
# share one scope vocabulary so rules/precedents can be tagged with either.
_CANONICAL = {
    "term", "ulip", "rider", "group", "savings_endowment", "pension_annuity",
    "par", "non_par",
}

# Aliases from other taggers (precedent ingest hints, prose) → canonical.
_ALIASES = {
    "term insurance": "term",
    "protection": "term",
    "unit linked": "ulip",
    "unit-linked": "ulip",
    "savings": "savings_endowment",
    "endowment": "savings_endowment",
    "pension": "pension_annuity",
    "annuity": "pension_annuity",
    "retirement": "pension_annuity",
    "participating": "par",
    "non-par": "non_par",
    "non par": "non_par",
    "nonpar": "non_par",
    "non-participating": "non_par",
}


# Tags the precedent ingest heuristic emits that are REAL and recognised, but
# deliberately do NOT scope to a product family — they are cross-cutting
# themes that legitimately appear across several families at once:
#
#   child — a variant of a ULIP (Smart Wealth Goal's child-wealth brochure,
#           UIN 116L214V01, product_category=ulip) AND a rider benefit
#           (Family Protect Rider – Parental Care, 116B056V01,
#           product_category=rider) added to term plans. Scoping a
#           child-benefit precedent to ONE family would hide it from the other.
#
# These already behaved as global, but only because normalize_category()
# happened to return None for them — i.e. correct by accident, via the
# "unmappable" path. Naming them makes the intent explicit, keeps a future
# vocabulary fix from silently narrowing retrieval, and tells SQL pushdown
# which raw values it must retain alongside the in-scope ones.
_CROSS_CUTTING = {"child"}


def is_cross_cutting(raw: Optional[str]) -> bool:
    """True for a recognised tag that intentionally does not product-scope."""
    return bool(raw) and str(raw).strip().lower() in _CROSS_CUTTING


def normalize_category(raw: Optional[str]) -> Optional[str]:
    """Map any known category spelling to the canonical fact-card enum.
    Unknown/unmappable tags return None — treated as GLOBAL by the contract
    (C7): a heuristic ingest tag must never overblock retrieval.

    Cross-cutting tags (see _CROSS_CUTTING) also return None: they are not
    product categories, so they must not enter a scope comparison."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in _CROSS_CUTTING:
        return None
    if s in _CANONICAL:
        return s
    return _ALIASES.get(s)


@dataclass(frozen=True)
class RetrievalScope:
    """The product envelope a submission is allowed to retrieve within."""
    uins: FrozenSet[str] = field(default_factory=frozenset)
    categories: FrozenSet[str] = field(default_factory=frozenset)
    resolved: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "uins": sorted(self.uins),
            "categories": sorted(self.categories),
            "resolved": self.resolved,
        }


def derive_segments(card: Dict[str, Any]) -> FrozenSet[str]:
    """Business market segment(s) of one fact card, derived deterministically
    from structural flags per the declared segregation
    (backend/data/product_segments.json):

      par     — is_participating
      ulip    — is_unit_linked
      term    — product_category == term (its own segment, never non_par)
      non_par — neither flag, on an individual savings/pension product
    """
    flags = card.get("structural_flags") or {}
    cat = normalize_category(card.get("product_category"))
    segs: set = set()
    if flags.get("is_unit_linked"):
        segs.add("ulip")
    if flags.get("is_participating"):
        segs.add("par")
    if cat == "term":
        segs.add("term")
    if (
        not flags.get("is_unit_linked")
        and not flags.get("is_participating")
        and cat in {"savings_endowment", "pension_annuity"}
    ):
        segs.add("non_par")
    return frozenset(segs)


def build_scope(product_match: List[Dict[str, Any]], fact_cards: Any) -> RetrievalScope:
    """Scope from the librarian's resolved products + their fact cards.

    The scope is the union of the fact-card product categories and the derived
    business market segments (C4): a unit-linked product/rider also accepts
    ulip-scoped items; a participating one accepts par-scoped items; a
    non-par savings/pension product accepts non_par-scoped items."""
    uins: set = set()
    cats: set = set()
    for m in product_match or []:
        uin = m.get("uin")
        if not uin:
            continue
        uins.add(uin)
        cards = (
            fact_cards.get_all(uin)
            if hasattr(fact_cards, "get_all")
            else [c for c in [fact_cards.get(uin)] if c]
        )
        for card in cards:
            cat = normalize_category(card.get("product_category"))
            if cat:
                cats.add(cat)
            cats |= derive_segments(card)
    return RetrievalScope(
        uins=frozenset(uins), categories=frozenset(cats), resolved=bool(uins)
    )


def _judge(scope: RetrievalScope, raw_tag: Optional[str]) -> Tuple[str, str]:
    """(verdict, reason) for one candidate under the contract."""
    if not scope.resolved:
        return "accepted", "scope_unresolved: no product identified; nothing rejected (C3)"
    if is_cross_cutting(raw_tag):
        # Recognised, deliberately non-scoping (e.g. 'child' spans a ULIP
        # variant AND a term-plan rider). Accepted on purpose, not because the
        # tag failed to parse — the reason string must not claim otherwise.
        return "accepted", f"global_cross_cutting: {raw_tag!r} is not product-scoping"
    cat = normalize_category(raw_tag)
    if cat is None:
        label = "untagged" if not raw_tag else f"unmappable tag {raw_tag!r}"
        return "accepted", f"global_{'untagged' if not raw_tag else 'unknown_tag'}: {label} (C2/C7)"
    if cat in scope.categories:
        return "accepted", f"category_match: {cat}"
    return "rejected", (
        f"category_conflict: item={cat} scope={sorted(scope.categories)} (C1)"
    )


def _validate(
    items: List[Dict[str, Any]],
    scope: RetrievalScope,
    *,
    corpus: str,
    tag_of,
    score_of,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    accepted: List[Dict[str, Any]] = []
    debug: List[Dict[str, Any]] = []
    for item in items or []:
        raw_tag = tag_of(item)
        verdict, reason = _judge(scope, raw_tag)
        debug.append({
            "corpus": corpus,
            "id": str(item.get("id")),
            "score": score_of(item),
            "scope_value": raw_tag,
            "verdict": verdict,
            "reason": reason,
        })
        if verdict == "accepted":
            accepted.append(item)
    rejected = len(debug) - len(accepted)
    if rejected:
        logger.info(
            "applicability: rejected %d/%d %s candidate(s) for scope %s",
            rejected, len(debug), corpus, sorted(scope.categories),
        )
    return accepted, debug


def validate_rules(
    rules: List[Dict[str, Any]],
    scope: RetrievalScope,
    product_line_by_id: Dict[str, Optional[str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Filter retrieved/fallback rules by `Rule.product_line` scope."""
    return _validate(
        rules, scope, corpus="rules",
        tag_of=lambda r: product_line_by_id.get(str(r.get("id"))) or r.get("product_line"),
        score_of=lambda r: r.get("rag_score"),
    )


def validate_precedents(
    precedents: List[Dict[str, Any]],
    scope: RetrievalScope,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Filter retrieved precedents by their `product_category` tag."""
    return _validate(
        precedents, scope, corpus="precedents",
        tag_of=lambda p: p.get("product_category"),
        score_of=lambda p: p.get("score"),
    )
