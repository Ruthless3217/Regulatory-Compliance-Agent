"""Canonical legal-entity registry (brand names, aliases, effective dates).

Mirrors the disclaimer registry pattern: deterministic JSON records under
backend/data/entities/, loaded once, looked up — never embedded, never
paraphrased. Exists because the 2025 "Bajaj Allianz Life → Bajaj Life" rename
was applied as a blind textual find-and-replace, which corrupted parser
regexes, disclaimer footers and seed rules (docs/audits/2026-07-28-brand-entity-
audit.md). All brand matching must go through these aliases so historical
creatives filed under the former name keep resolving.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class EntityRegistry:
    def __init__(self, entities_dir: Union[str, Path]):
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._load(Path(entities_dir))

    def _load(self, d: Path) -> None:
        if not d.is_dir():
            logger.warning("EntityRegistry: dir not found: %s", d)
            return
        for path in sorted(d.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("EntityRegistry: skipping malformed %s: %s", path.name, e)
                continue
            if not raw.get("id") or not raw.get("canonical_name"):
                logger.error("EntityRegistry: %s missing id/canonical_name; skipping", path.name)
                continue
            self._by_id[raw["id"]] = raw

    def get(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self._by_id.get(entity_id)

    def aliases(self, entity_id: str) -> List[Dict[str, Any]]:
        return list((self._by_id.get(entity_id) or {}).get("aliases") or [])

    def alias_regex(self, entity_id: str) -> str:
        """Regex matching every era of the entity's name (case-insensitively by
        callers). Empty string when the entity is unknown."""
        return str((self._by_id.get(entity_id) or {}).get("alias_regex") or "")


_singleton: Optional[EntityRegistry] = None


def get_entity_registry() -> EntityRegistry:
    global _singleton
    if _singleton is None:
        base = Path(__file__).resolve().parents[2] / "data" / "entities"
        _singleton = EntityRegistry(base)
    return _singleton


def _reset_singleton_for_testing() -> None:  # pragma: no cover
    global _singleton
    _singleton = None
