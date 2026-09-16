"""Deterministic product fact-card lookup.

Loads the hand-curated product fact cards (backend/data/product_fact_cards/*.json)
into a UIN-keyed dict. These are authoritative, exact facts + compliance
guardrails — NOT embedded; they are looked up by UIN and injected verbatim into
the analysis prompt as deterministic ground truth.

A card answers ONLY its own plan `uin`. A rider UIN is recognised (it is in
`known_uins`) but resolves to no card of its own — use `parents_of_rider()` for
the relationship. A UIN carrying several variant cards resolves to none of them;
`resolve_one()` reports the ambiguity. Neither is a lookup miss, and telling
them apart is what stops a rider being graded against its parent's guardrails.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

_VERSION_SUFFIX = re.compile(
    r"\s+(?:i{1,3}v?|iv|v|vi{0,3}|vii|[0-9]+)$",
    re.IGNORECASE,
)


def _base_product_name(name: str) -> str:
    value = re.sub(r"\s+", " ", name or "").strip().lower()
    previous = None
    while value != previous:
        previous = value
        value = _VERSION_SUFFIX.sub("", value)
    return value


@dataclass(frozen=True)
class CardResolution:
    """What a UIN resolves to, and why — never a silently-chosen card.

    `card` is set only when exactly one fact card answers the UIN. `reason` is
    one of: resolved | ambiguous_variants | rider_without_card | unknown.
    `candidates` keeps every variant so a reviewer (or a later
    Product/ProductVariant model) can see what was in contention.
    """
    uin: str
    card: Optional[Dict[str, Any]]
    candidates: List[Dict[str, Any]]
    ambiguous: bool
    rider_parents: List[Dict[str, Any]]
    reason: str


class FactCardService:
    def __init__(self, cards_dir: Union[str, Path]):
        # There is deliberately no `_by_uin` single-card dict any more: every
        # lookup goes through resolve_one(), which refuses to pick among
        # variants. A dict keyed by a non-unique UIN is the bug, not a cache.
        self._all_by_uin: Dict[str, List[Dict[str, Any]]] = {}
        self._primary_uins: set[str] = set()
        self._rider_parents_by_uin: Dict[str, List[Dict[str, Any]]] = {}
        self._cards: List[Dict[str, Any]] = []
        self._declared_without_cards: List[Dict[str, Any]] = []
        self._cards_loaded_ok = False
        self._catalog_loaded_ok = False
        self._load_errors: List[str] = []
        self._load(Path(cards_dir))

    def _load(self, cards_dir: Path) -> None:
        if not cards_dir.is_dir():
            logger.warning("FactCardService: cards dir not found: %s", cards_dir)
            self._load_errors.append("cards_dir_missing")
            return
        for path in sorted(cards_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("FactCardService: skipping malformed card %s: %s", path.name, e)
                self._load_errors.append(f"malformed_card:{path.name}")
                continue
            uin = card.get("uin")
            if not uin:
                logger.warning("FactCardService: card %s has no uin; skipping", path.name)
                self._load_errors.append(f"missing_uin:{path.name}")
                continue
            self._cards.append(card)
            self._primary_uins.add(uin)
            self._all_by_uin.setdefault(uin, []).append(card)
            for rider in (card.get("rider_uins") or []):
                # The rider→parent GRAPH is kept; the rider→parent CARD
                # substitution is not. `_by_uin.setdefault(rider, card)` used to
                # live here, which made get("116N216V01") return "Bajaj Life
                # Smart Secure ROP" — a rider silently inheriting a different
                # product's guardrails, which is exactly the false grounding the
                # rider gate exists to prevent. Ask for the relationship
                # explicitly via parents_of_rider().
                self._rider_parents_by_uin.setdefault(rider, []).append(card)
        self._cards_loaded_ok = bool(self._cards) and not self._load_errors
        # Product/variant records sharing one UIN (real data: 116L211V02 Supreme
        # Gold/Horizon; 116L214V01 Smart Wealth Goal VI ×3, whose guardrails and
        # offers_guaranteed_benefits DISAGREE). get() used to return the
        # last-sorted card — on 116L214V01 that was the one variant with
        # offers_guaranteed_benefits=False, so filename order decided whether
        # "guaranteed" wording was permissible. It now resolves to NO card and
        # resolve_one() reports the candidates.
        for uin, cards in self._all_by_uin.items():
            if len(cards) > 1:
                names = [c.get("product_name") or "?" for c in cards]
                logger.warning(
                    "FactCardService: UIN collision — %s maps to %d cards %s; "
                    "it resolves to none of them. Use resolve_one() for the "
                    "ambiguity or get_all() for every variant",
                    uin, len(cards), names,
                )
        self._load_declared_gaps(cards_dir.parent / "product_segments.json")

    def _load_declared_gaps(self, catalog_path: Path) -> None:
        """Load business-declared products that still lack a fact card."""
        if not catalog_path.is_file():
            self._load_errors.append("product_catalog_missing")
            return
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "FactCardService: could not load declared product catalog %s: %s",
                catalog_path,
                exc,
            )
            self._load_errors.append("product_catalog_malformed")
            return

        card_names = [
            _base_product_name(card.get("product_name") or "")
            for card in self._cards
        ]
        for segment, products in (catalog.get("segments") or {}).items():
            for product in products or []:
                names = [product.get("name") or ""] + list(product.get("aliases") or [])
                bases = {_base_product_name(name) for name in names if name}
                if any(
                    card_name.startswith(base)
                    for card_name in card_names
                    for base in bases
                ):
                    continue
                self._declared_without_cards.append({
                    "name": product.get("name") or "",
                    "aliases": list(product.get("aliases") or []),
                    "segment": segment,
                })
        self._catalog_loaded_ok = True

    @property
    def collisions(self) -> Dict[str, List[Dict[str, Any]]]:
        """UINs that map to more than one fact card → all their cards."""
        return {u: list(cs) for u, cs in self._all_by_uin.items() if len(cs) > 1}

    def all_products(self) -> List[Dict[str, Any]]:
        return [
            {
                "uin": c["uin"],
                "product_name": c.get("product_name") or "",
                # Marketing/feature aliases (e.g. eTouch II's "Health Management
                # Services") so feature collateral without a product name still
                # resolves to its product.
                "aliases": list(c.get("marketing_aliases") or []),
            }
            for c in self._cards
        ]

    @property
    def known_uins(self) -> set[str]:
        """Every UIN the corpus RECOGNISES — plan UINs plus every rider UIN a
        card cites.

        Recognition is not resolution. A rider UIN is known (so it is never
        reported as an unknown identifier) while resolving to no card of its
        own (so it is reported as a grounding gap instead). This used to read
        `set(self._by_uin)` and depended on the rider→parent substitution to
        include riders at all; with that gone it has to say so explicitly, or
        every rider UIN would be double-reported as unknown_uins AND
        rider_uins_without_fact_cards.
        """
        return set(self._primary_uins) | set(self._rider_parents_by_uin)

    @property
    def availability_issues(self) -> List[str]:
        """Reasons deterministic product grounding is not production-ready."""
        issues = list(self._load_errors)
        if not self._cards_loaded_ok and "cards_unavailable" not in issues:
            issues.append("cards_unavailable")
        if not self._catalog_loaded_ok and not any(
            issue.startswith("product_catalog_") for issue in issues
        ):
            issues.append("product_catalog_unavailable")
        return issues

    @property
    def rider_uins_without_fact_cards(self) -> set[str]:
        """Referenced rider UINs that have no canonical standalone card.

        A parent plan mentioning a rider proves compatibility, not the rider's
        own benefits/guardrails. These identifiers must route to review instead
        of silently injecting whichever parent happened to load first.
        """
        return set(self._rider_parents_by_uin) - self._primary_uins

    @property
    def rider_parent_collisions(self) -> Dict[str, List[Dict[str, Any]]]:
        """Rider-only UINs referenced by more than one parent fact card."""
        return {
            uin: list(cards)
            for uin, cards in self._rider_parents_by_uin.items()
            if uin not in self._primary_uins and len(cards) > 1
        }

    @property
    def declared_without_fact_cards(self) -> List[Dict[str, Any]]:
        """Business-declared products that cannot be deterministically grounded."""
        return [dict(product) for product in self._declared_without_cards]

    def resolve_one(self, uin: str) -> CardResolution:
        """What this UIN authoritatively identifies — or why it does not.

        The one entry point that never guesses. `get()` used to be total over a
        key that is not unique: 116L214V01 maps to three variant cards whose
        guardrails disagree (offers_guaranteed_benefits is False on one and
        True on two), and it returned whichever sorted last — the permissive
        one. A UIN with more than one card is AMBIGUOUS, and saying so is the
        only safe answer.
        """
        candidates = list(self._all_by_uin.get(uin) or [])
        parents = list(self._rider_parents_by_uin.get(uin) or [])
        if len(candidates) == 1:
            return CardResolution(uin, candidates[0], candidates, False,
                                  parents, "resolved")
        if len(candidates) > 1:
            # Debug, not warning: lookup_many() runs per chunk, and the
            # collision is already announced once at load time.
            logger.debug(
                "FactCardService: %s is ambiguous — %d variant cards %s; "
                "refusing to pick one", uin, len(candidates),
                [c.get("product_name") for c in candidates],
            )
            return CardResolution(uin, None, candidates, True, parents,
                                  "ambiguous_variants")
        if parents:
            return CardResolution(uin, None, [], False, parents,
                                  "rider_without_card")
        return CardResolution(uin, None, [], False, [], "unknown")

    def get(self, uin: str) -> Optional[Dict[str, Any]]:
        """The single authoritative card for this UIN, else None.

        None now means one of three things — unknown UIN, a rider with no card
        of its own, or a UIN whose variants disagree. Callers that need to tell
        them apart must use resolve_one().
        """
        return self.resolve_one(uin).card

    def get_all(self, uin: str) -> List[Dict[str, Any]]:
        """Every card for this UIN (variants included), [] when unknown."""
        return list(self._all_by_uin.get(uin) or [])

    def parents_of_rider(self, uin: str) -> List[Dict[str, Any]]:
        """The plan cards that cite this UIN among their `rider_uins`.

        Explicit, and never a substitute for the rider's own record: a parent
        proves compatibility, not the rider's benefits or guardrails.
        """
        return list(self._rider_parents_by_uin.get(uin) or [])

    def lookup_many(self, uins: Iterable[str]) -> List[Dict[str, Any]]:
        """Cards for these UINs, skipping anything that cannot be resolved.

        An ambiguous or rider-only UIN contributes NOTHING rather than a
        stand-in. Silence here is correct: the run's own gates
        (product_ambiguous / rider_uins_without_fact_cards) are what tell the
        reviewer the grounding is missing.
        """
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for u in uins:
            card = self.resolve_one(u).card
            if card is None:
                continue
            key = card["uin"]
            if key in seen:
                continue
            seen.add(key)
            out.append(card)
        return out


_singleton: Optional[FactCardService] = None


def get_fact_card_service() -> FactCardService:
    global _singleton
    if _singleton is None:
        from app.config import settings
        _singleton = FactCardService(Path(settings.product_fact_cards_dir))
    return _singleton


def _reset_singleton_for_testing() -> None:  # pragma: no cover
    """Clear the cached singleton so tests can rebuild it against a different dir."""
    global _singleton
    _singleton = None
