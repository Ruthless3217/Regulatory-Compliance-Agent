"""Deterministic product fact-card lookup.

Loads the hand-curated product fact cards (backend/data/product_fact_cards/*.json)
into a UIN-keyed dict. These are authoritative, exact facts + compliance
guardrails — NOT embedded; they are looked up by UIN and injected verbatim into
the analysis prompt as deterministic ground truth. Both a card's plan `uin` and
every `rider_uins` entry resolve to the same card.
"""
from __future__ import annotations

import json
import logging
import re
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


class FactCardService:
    def __init__(self, cards_dir: Union[str, Path]):
        self._by_uin: Dict[str, Dict[str, Any]] = {}
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
            self._by_uin[uin] = card
            self._all_by_uin.setdefault(uin, []).append(card)
            for rider in (card.get("rider_uins") or []):
                # Plan UIN wins if a rider UIN collides with a plan UIN.
                self._by_uin.setdefault(rider, card)
                self._rider_parents_by_uin.setdefault(rider, []).append(card)
        self._cards_loaded_ok = bool(self._cards) and not self._load_errors
        # Product/variant records sharing one UIN (real data: 116L211V02 Supreme
        # Gold/Horizon; 116L214V01 Smart Wealth Goal VI ×3, whose guardrails and
        # offers_guaranteed_benefits DISAGREE). get() keeps returning the
        # last-sorted card for compatibility, but the collision must be loud and
        # queryable so resolution can expose the ambiguity instead of silently
        # grading against an arbitrary variant.
        for uin, cards in self._all_by_uin.items():
            if len(cards) > 1:
                names = [c.get("product_name") or "?" for c in cards]
                logger.warning(
                    "FactCardService: UIN collision — %s maps to %d cards %s; "
                    "get() returns the last-sorted card, use get_all() to see "
                    "every variant", uin, len(cards), names,
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
        """Every plan/rider UIN that resolves to at least one fact card."""
        return set(self._by_uin)

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

    def get(self, uin: str) -> Optional[Dict[str, Any]]:
        return self._by_uin.get(uin)

    def get_all(self, uin: str) -> List[Dict[str, Any]]:
        """Every card for this UIN (variants included), [] when unknown."""
        return list(self._all_by_uin.get(uin) or [])

    def lookup_many(self, uins: Iterable[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for u in uins:
            card = self._by_uin.get(u)
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
