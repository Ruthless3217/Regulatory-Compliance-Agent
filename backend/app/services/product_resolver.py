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
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_UIN_RE = re.compile(r"\b\d{3}[A-Z]\d{3}V\d{2}\b", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z0-9]+")
_WORD_RE_ANY_CASE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

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


def _token_spans(text: str) -> List[Tuple[str, int, int]]:
    """(token, char_start, char_end) for every token, in document order.

    The character span is what lets the edition check honour the document's
    own punctuation: "eTouch. Our" and "eTouch, a plan" delimit the name;
    "Goal Plus" does not.
    """
    # Matched case-insensitively on the ORIGINAL text so the offsets are exact;
    # lowercasing first could shift them for some Unicode letters.
    return [
        (m.group(0).lower(), m.start(), m.end())
        for m in _WORD_RE_ANY_CASE.finditer(text or "")
    ]


def _token_positions(text: str) -> Dict[str, List[int]]:
    """token -> every position it occupies, built once per document."""
    positions: Dict[str, List[int]] = {}
    for index, token in enumerate(_tokens(text)):
        positions.setdefault(token, []).append(index)
    return positions


def _name_vocabulary(names: List[str], brand: FrozenSet[str]) -> FrozenSet[str]:
    """Every non-brand, non-version token any curated product name uses.

    This is what "name-like" means for the edition check, and it is derived
    from the corpus in the same way `_brand_tokens` is — never from a
    hand-written list. On the real corpus it contains `plus`, `elite`,
    `platinum`, `variant`, `rop`, `gold`, `horizon`; it does not contain
    `is`, `our`, `offers`, `protects`.
    """
    vocabulary: Set[str] = set()
    for name in names:
        for token in _tokens(name):
            if token not in brand and not _VERSION_TOKEN_RE.match(token):
                vocabulary.add(token)
    return frozenset(vocabulary)


def _identity_mentions(
    positions: Dict[str, List[int]], identity: Set[str], window: int
) -> List[Tuple[int, int]]:
    """Every (first_index, last_index) where all identity tokens co-occur
    within `window` — each local mention, not just the tightest one.

    A document can name a product several times with different neighbours
    ("...Goal III..." here, "...Goal Plus..." there), and the edition verdict
    must weigh all of them.
    """
    if not identity or any(token not in positions for token in identity):
        return []
    anchor = min(identity, key=lambda t: len(positions[t]))
    others = [positions[t] for t in identity if t != anchor]
    mentions: List[Tuple[int, int]] = []
    for at in positions[anchor]:
        lo, hi = at, at
        for occurrences in others:
            nearest = min(occurrences, key=lambda i: abs(i - at))
            if abs(nearest - at) > window:
                break
            lo, hi = min(lo, nearest), max(hi, nearest)
        else:
            if hi - lo <= window and (lo, hi) not in mentions:
                mentions.append((lo, hi))
    return mentions


def _adjacent_in_run(
    spans: List[Tuple[str, int, int]], text: str, index: int, step: int
) -> Optional[str]:
    """The token one step away, if only whitespace separates it — else None.

    Punctuation ends a product name in the document's own grammar, so a token
    across a period, comma, dash or bracket is not in the edition slot.
    """
    neighbour = index + step
    if neighbour < 0 or neighbour >= len(spans):
        return None
    a, b = sorted((index, neighbour))
    between = text[spans[a][2]:spans[b][1]]
    if between.strip():
        return None
    return spans[neighbour][0]


def _classify_edition(
    text: str,
    spans: List[Tuple[str, int, int]],
    positions: Dict[str, List[int]],
    name: str,
    brand: FrozenSet[str],
    vocabulary: FrozenSet[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    """(verdict, candidate_edition, document_edition) for one candidate name.

    Only a candidate whose name carries a version token can be contradicted:
    version stripping is the one place the resolver deliberately matches less
    than the full name, so it is the one place the document can say more than
    the candidate does. Each local mention is judged by the token adjacent to
    the identity phrase in the same punctuation-free run — the EDITION SLOT,
    which on this corpus is where 13 of 16 version-bearing names put their
    version token:

        confirmed     the candidate's own version token is in the slot
        contradicted  a name-like token the candidate does not have is there
        neutral       nothing name-like is there

    One confirmation wins (a document naming both "Goal III" and "Goal Plus"
    is a two-product document). Otherwise one contradiction refuses. Otherwise
    the neutral case keeps the existing no-version recall ("eTouch" resolves
    to "eTouch II"). Never a similarity threshold, never a UIN letter.
    """
    full_tokens = set(_tokens(name))
    versions = {t for t in full_tokens if _VERSION_TOKEN_RE.match(t)}
    if not versions:
        return "not_applicable", None, None
    identity = _identity_tokens(name, brand)
    candidate_edition = ",".join(sorted(versions))
    contradiction: Optional[str] = None
    for lo, hi in _identity_mentions(positions, identity, _IDENTITY_WINDOW_TOKENS):
        # Both slots of ONE mention are weighed before it counts as anything:
        # "Group Fortune Gain II" carries a confirming suffix AND a foreign
        # prefix, and mixed evidence is not proof — it fails closed.
        confirmed_by: Optional[str] = None
        contradicted_by: Optional[str] = None
        for slot in (_adjacent_in_run(spans, text, hi, +1),
                     _adjacent_in_run(spans, text, lo, -1)):
            if slot is None or slot in brand or slot in full_tokens:
                if slot in versions:
                    confirmed_by = slot
                continue
            if _VERSION_TOKEN_RE.match(slot) or slot in vocabulary:
                contradicted_by = contradicted_by or slot
        if confirmed_by and not contradicted_by:
            return "confirmed", candidate_edition, confirmed_by
        if contradicted_by:
            contradiction = contradiction or contradicted_by
    if contradiction:
        return "contradicted", candidate_edition, contradiction
    return "neutral", candidate_edition, None


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
        # Products the document names by base but contradicts by edition
        # ("Goal Plus" against the card for "Goal III"). The corpus has no
        # card for what the document actually names.
        "edition_conflicts": edition_conflicts(text, fact_card_service),
    }


def edition_conflicts(text: str, fact_card_service) -> List[Dict[str, Any]]:
    """Candidates the document names by base but contradicts by edition.

    "Invest Protect Goal Plus" against the card for "Invest Protect Goal III":
    the identity phrase is locally present and the similarity score is high,
    but the document's edition slot says `plus` and the candidate's `iii`
    appears nowhere. These are NOT resolved — and they are not silently dropped
    either. The document is naming a product the corpus has no card for, and
    the reviewer must see that. Surfaced through unresolved_product_signals as
    `edition_conflicts` and on the run as an `edition_conflicts` warning.
    """
    _, conflicts = _resolve(text or "", fact_card_service)
    return conflicts


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
    ranked, _ = _resolve(text or "", fact_card_service, min_fuzzy_score=min_fuzzy_score)
    if max_matches is not None and len(ranked) > max_matches:
        logger.info(
            "product_resolver: %d products matched; caller capped to %d (dropped %s)",
            len(ranked), max_matches, [m["uin"] for m in ranked[max_matches:]],
        )
        return ranked[:max_matches]
    return ranked


def _resolve(
    text: str,
    fact_card_service,
    *,
    min_fuzzy_score: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(ranked matches, edition conflicts). One pass, so the two never disagree.

    `min_fuzzy_score` falls back to settings when the caller only wants the
    conflicts — the contradiction check runs before the score is consulted, so
    the reported conflicts do not depend on the threshold anyway.
    """
    if min_fuzzy_score is None:
        from app.config import settings as _settings
        min_fuzzy_score = _settings.kb_min_fuzzy_score
    conflicts: List[Dict[str, Any]] = []
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
    text_spans = _token_spans(text)
    vocabulary = _name_vocabulary([p.get("product_name") or "" for p in products], brand)
    best_fuzzy: Dict[str, Dict[str, Any]] = {}

    def _consider(uin: str, display_name: str, confidence: float, method: str) -> None:
        entry = _entry(uin, display_name, confidence, method)
        prev = best_fuzzy.get(uin)
        if prev is None or entry["confidence"] > prev["confidence"]:
            best_fuzzy[uin] = entry

    # Version siblings: a document that cites "Guaranteed Pension Goal II
    # (UIN 116N187V11)" names ONE product. The V09 card carries the identical
    # name, so name matching would resolve it too and the run would ground on
    # two editions of one plan. Any card whose identity equals a UIN-matched
    # card's is that product under another version and is skipped here; a
    # superseded card whose successor is in the corpus is never resolved by
    # name at all — only its exact UIN may bring it back.
    uin_matched_identities = {
        frozenset(_identity_tokens(name_by_uin.get(u, ""), brand)) for u in seen_uins
    }
    uin_matched_identities.discard(frozenset())
    for p in products:
        if p["uin"] in seen_uins:
            continue
        name = (p.get("product_name") or "").strip()
        if name and frozenset(_identity_tokens(name, brand)) in uin_matched_identities:
            continue
        successor = str(p.get("superseded_by") or "").upper()
        if successor and successor in known_uins:
            continue
        if name and _document_names(text_positions, name, brand):
            # Locality proved the base name is here. Before similarity is even
            # consulted, the document's edition evidence gets the last word:
            # a high score for "Invest Protect Goal III" against "Invest
            # Protect Goal Plus" is similarity, not identity. A contradiction
            # refuses the NAME path only — the alias loop below still runs,
            # because an exact alias outranks local name identity.
            verdict, cand_ed, doc_ed = _classify_edition(
                text, text_spans, text_positions, name, brand, vocabulary
            )
            if verdict == "contradicted":
                logger.warning(
                    "product_resolver: %s (%s) named by base only — the document "
                    "says edition %r where the card says %r; not resolved by name",
                    name, p["uin"], doc_ed, cand_ed,
                )
                conflicts.append({
                    "uin": p["uin"], "candidate": name,
                    "candidate_edition": cand_ed, "document_edition": doc_ed,
                })
            else:
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

    # Conflicts are reported even when an alias went on to resolve the same
    # UIN: the document's name evidence still contradicts that card, and the
    # reviewer should see both facts. An exact UIN never reaches here (the
    # product loop skips it), and a confirmed sibling mention never appends
    # one. The one thing that IS filtered: a conflict the corpus itself
    # explains. "Invest Protect Goal Plus" contradicts the Goal III card, but
    # the Smart Secure card (116L215V01) carries that exact name as an alias
    # and resolved in this same pass — the document named a product the corpus
    # DOES have a card for, so there is no gap to warn about. Only a card that
    # resolved may explain a conflict; an alias on an unresolved card is not
    # evidence of anything.
    resolved = matches + fuzzy
    explained = _explained_conflicts(conflicts, resolved, products, brand)
    conflicts = [c for c in conflicts if id(c) not in explained]
    return resolved, sorted(conflicts, key=lambda c: c["uin"])


def _explained_conflicts(
    conflicts: List[Dict[str, Any]],
    resolved: List[Dict[str, Any]],
    products: List[Dict[str, Any]],
    brand: FrozenSet[str],
) -> Set[int]:
    """ids of conflicts whose document-side name is a known alias of a product
    that resolved to a DIFFERENT UIN in this pass.

    The document-side name is the candidate's name with the candidate's edition
    replaced by the document's ("... Goal III" -> "... Goal Plus"); it is
    compared by identity tokens (brand and version tokens stripped) against
    the product name and every alias of each resolved card, so "Bajaj Life
    Invest Protect Goal Plus" and "Invest Protect Goal Plus - Elite Variant"
    both explain the same conflict.
    """
    if not conflicts or not resolved:
        return set()
    resolved_uins = {str(m.get("uin") or "").upper() for m in resolved}
    identities: Dict[str, Set[FrozenSet[str]]] = {}
    for p in products:
        uin = str(p.get("uin") or "").upper()
        if uin not in resolved_uins:
            continue
        for name in [p.get("product_name") or ""] + list(p.get("aliases") or []):
            tokens = _identity_tokens(name, brand)
            if tokens:
                identities.setdefault(uin, set()).add(frozenset(tokens))
    out: Set[int] = set()
    for c in conflicts:
        cand_ed = str(c.get("candidate_edition") or "")
        doc_ed = str(c.get("document_edition") or "")
        if not cand_ed or not doc_ed:
            continue
        doc_name = re.sub(
            rf"\b{re.escape(cand_ed)}\b", doc_ed, c.get("candidate") or "",
            flags=re.IGNORECASE,
        )
        # The document's edition word is part of THIS product's identity, so
        # it must survive the version-token strip that _identity_tokens does.
        doc_identity = frozenset(
            t for t in _tokens(doc_name) if t not in brand and t != cand_ed.lower()
        )
        if not doc_identity:
            continue
        for uin, names in identities.items():
            if uin == str(c.get("uin") or "").upper():
                continue
            if any(
                doc_identity == frozenset(
                    t for t in _tokens(" ".join(n)) if t != cand_ed.lower()
                ) or doc_identity == n
                for n in names
            ):
                out.add(id(c))
                break
    return out
