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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


class FactCardService:
    def __init__(self, cards_dir: Path):
        self._by_uin: Dict[str, Dict[str, Any]] = {}
        self._cards: List[Dict[str, Any]] = []
        self._load(Path(cards_dir))

    def _load(self, cards_dir: Path) -> None:
        if not cards_dir.is_dir():
            logger.warning("FactCardService: cards dir not found: %s", cards_dir)
            return
        for path in sorted(cards_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("FactCardService: skipping malformed card %s: %s", path.name, e)
                continue
            uin = card.get("uin")
            if not uin:
                logger.warning("FactCardService: card %s has no uin; skipping", path.name)
                continue
            self._cards.append(card)
            self._by_uin[uin] = card
            for rider in (card.get("rider_uins") or []):
                # Plan UIN wins if a rider UIN collides with a plan UIN.
                self._by_uin.setdefault(rider, card)

    def all_products(self) -> List[Dict[str, str]]:
        return [{"uin": c["uin"], "product_name": c.get("product_name") or ""} for c in self._cards]

    def get(self, uin: str) -> Optional[Dict[str, Any]]:
        return self._by_uin.get(uin)

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
