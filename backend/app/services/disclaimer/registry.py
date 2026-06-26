"""Deterministic disclaimer registry.

Loads the curated disclaimer library (backend/data/disclaimers/*.json) into
memory. Each file is one regulator-mandated disclaimer: verbatim text +
trigger config + per-disclaimer severity. Mirrors fact_card_service: ground
truth, looked up — never embedded, never paraphrased.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("id", "type", "text", "severity", "altered_severity", "triggers", "precedence")
_VALID_SEVERITIES = {"critical", "high", "moderate", "low"}


@dataclass(frozen=True)
class Disclaimer:
    id: str
    type: str
    text: str
    anchors: List[str]
    severity: str
    altered_severity: str
    triggers: Dict[str, Any]
    present_threshold: float
    altered_threshold: float
    precedence: int
    source: str


class DisclaimerRegistry:
    def __init__(self, disclaimers_dir: Union[str, Path]):
        self._by_id: Dict[str, Disclaimer] = {}
        self._loaded_ok = False
        self._load(Path(disclaimers_dir))

    def _load(self, d: Path) -> None:
        if not d.is_dir():
            logger.warning("DisclaimerRegistry: dir not found: %s", d)
            return
        for path in sorted(d.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("DisclaimerRegistry: skipping malformed %s: %s", path.name, e)
                continue
            missing = [f for f in _REQUIRED_FIELDS if f not in raw]
            if missing:
                logger.warning("DisclaimerRegistry: %s missing %s; skipping", path.name, missing)
                continue
            bad_sev = next(
                (v for v in (raw["severity"], raw["altered_severity"]) if v not in _VALID_SEVERITIES),
                None,
            )
            if bad_sev is not None:
                logger.warning(
                    "DisclaimerRegistry: %s has invalid severity %r; skipping", path.name, bad_sev
                )
                continue
            match = raw.get("match") or {}
            disc = Disclaimer(
                id=raw["id"],
                type=raw["type"],
                text=raw["text"],
                anchors=list(raw.get("anchors") or []),
                severity=raw["severity"],
                altered_severity=raw["altered_severity"],
                triggers=raw["triggers"] or {},
                present_threshold=float(match.get("present_threshold", 0.85)),
                altered_threshold=float(match.get("altered_threshold", 0.45)),
                precedence=int(raw["precedence"]),
                source=raw.get("source", ""),
            )
            self._by_id[disc.id] = disc
        self._loaded_ok = len(self._by_id) > 0

    @property
    def loaded_ok(self) -> bool:
        return self._loaded_ok

    def all(self) -> List[Disclaimer]:
        return list(self._by_id.values())

    def get(self, disclaimer_id: str) -> Optional[Disclaimer]:
        return self._by_id.get(disclaimer_id)


_singleton: Optional[DisclaimerRegistry] = None


def get_disclaimer_registry() -> DisclaimerRegistry:
    global _singleton
    if _singleton is None:
        from app.config import settings
        _singleton = DisclaimerRegistry(Path(settings.disclaimers_dir))
    return _singleton


def _reset_singleton_for_testing() -> None:  # pragma: no cover
    global _singleton
    _singleton = None
