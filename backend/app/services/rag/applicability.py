"""Deterministic regulatory-applicability layer for retrieval (RETRIEVAL_RCA.md).

Product identity is resolved BEFORE retrieval (librarian node), and the corpora
already carry scope metadata (`Rule.product_line`,
`precedent_cases.product_category`) — this module is the missing consumer. It
builds a RetrievalScope from the resolved products and validates every
retrieved candidate against it, so regulatory applicability is decided
deterministically before semantic similarity is allowed to matter.

Current fail-closed contract: explicit global and recognised cross-cutting
tags are accepted across products; missing or unmappable scope metadata is
rejected for audit and curation; product-scoped candidates require a resolved
submission product and a matching category/segment. Every decision is returned
as a debug record so the retrieval debugger explains why a candidate entered
or was refused.
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
# These are accepted globally only because they are named here. Unknown tags
# remain fail-closed so an ingest typo cannot silently broaden applicability.
_CROSS_CUTTING = {"child"}
_EXPLICIT_GLOBAL = {"global", "all_products"}


def is_cross_cutting(raw: Optional[str]) -> bool:
    """True for a recognised tag that intentionally does not product-scope."""
    return bool(raw) and str(raw).strip().lower() in _CROSS_CUTTING


def normalize_category(raw: Optional[str]) -> Optional[str]:
    """Map any known category spelling to the canonical fact-card enum.
    Unknown/unmappable tags return None and are rejected by the applicability
    judge until an owner explicitly classifies them.

    Cross-cutting tags (see _CROSS_CUTTING) also return None: they are not
    product categories, but the judge recognises and accepts them explicitly."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in _CROSS_CUTTING:
        return None
    if s in _CANONICAL:
        return s
    return _ALIASES.get(s)


def _spellings(token: str) -> set:
    """Every casing of `token` a corpus column is known to hold.

    The taggers disagree on case by design and history: precedent ingest writes
    the title/upper labels of its hint table ('ULIP', 'Non-Par', 'Pension'),
    while the rule/submission API and the backfill script write canonical
    lowercase ('ulip', 'non_par', 'pension_annuity'). SQL `IN` is
    case-sensitive, so a pushdown filter built from canonical values alone
    would match zero ingested precedents.
    """
    return {token, token.lower(), token.upper(), token.title()}


@dataclass(frozen=True)
class RetrievalScope:
    """The product envelope a submission is allowed to retrieve within."""
    uins: FrozenSet[str] = field(default_factory=frozenset)
    categories: FrozenSet[str] = field(default_factory=frozenset)
    resolved: bool = False
    declared_product_line: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "uins": sorted(self.uins),
            "categories": sorted(self.categories),
            "resolved": self.resolved,
            "declared_product_line": self.declared_product_line,
        }


def scope_filter_values(scope: RetrievalScope) -> Optional[List[Optional[str]]]:
    """Value list for a SQL pushdown filter on a product-scope column
    (`precedent_cases.product_category`, `rag_*.product_line`).

    Construction rule, exactly:
      * unresolved scope -> None, meaning NO filter at all (never over-restrict
        a run whose product could not be resolved)
      * otherwise: every canonical category in the scope, expanded to each raw
        spelling that normalizes onto it (its aliases), in all four casings
      * plus the explicit-global and cross-cutting tokens, same expansion —
        they apply to every product and must never be cut in SQL
      * plus None, so untagged rows stay retrievable (C2/C7 fail-open; the
        applicability judge is the strict gate and still rejects them)

    Deliberately coarse: this cut only stops wrong-product rows from eating
    recall-pool slots. It is not the applicability decision.
    """
    if not scope.resolved:
        return None
    values: set = set()
    for cat in scope.categories:
        values |= _spellings(cat)
        values |= {
            s
            for alias, canon in _ALIASES.items() if canon == cat
            for s in _spellings(alias)
        }
    for token in _EXPLICIT_GLOBAL | _CROSS_CUTTING:
        values |= _spellings(token)
    return sorted(values) + [None]


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


def build_scope(
    product_match: List[Dict[str, Any]],
    fact_cards: Any,
    declared_product_line: Optional[str] = None,
) -> RetrievalScope:
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
    declared = (declared_product_line or "").strip().lower()
    # A family declaration is authoritative only when no exact product was
    # detected. If both exist, preprocessing verifies they agree before this
    # scope is consumed. Explicit global means genuinely product-neutral copy
    # and deliberately admits global/cross-cutting evidence only.
    if not uins:
        declared_category = normalize_category(declared)
        if declared_category:
            cats.add(declared_category)
    declaration_resolves = bool(
        declared in _EXPLICIT_GLOBAL or normalize_category(declared)
    )
    return RetrievalScope(
        uins=frozenset(uins),
        categories=frozenset(cats),
        resolved=bool(uins) or declaration_resolves,
        declared_product_line=declared or None,
    )


def _judge(scope: RetrievalScope, raw_tag: Optional[str]) -> Tuple[str, str]:
    """(verdict, reason) for one candidate under the contract."""
    if raw_tag and str(raw_tag).strip().lower() in _EXPLICIT_GLOBAL:
        return "accepted", f"global_explicit: {raw_tag!r} applies to every product"
    if is_cross_cutting(raw_tag):
        # Recognised, deliberately non-scoping (e.g. 'child' spans a ULIP
        # variant AND a term-plan rider). Accepted on purpose, not because the
        # tag failed to parse — the reason string must not claim otherwise.
        return "accepted", f"global_cross_cutting: {raw_tag!r} is not product-scoping"
    cat = normalize_category(raw_tag)
    if cat is None:
        label = "untagged" if not raw_tag else f"unmappable tag {raw_tag!r}"
        return "rejected", (
            f"scope_metadata_missing: {label}; explicitly classify or mark global "
            "(C2/C7)"
        )
    if not scope.resolved:
        return "rejected", (
            f"scope_unresolved: cannot prove {cat!r} applies without a resolved "
            "product (C3)"
        )
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
