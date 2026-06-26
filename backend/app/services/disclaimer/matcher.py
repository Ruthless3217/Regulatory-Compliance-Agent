"""Document-wide verbatim matching for mandated disclaimers.

Normalizes both sides, then takes the max of a token-set ratio over the whole
document and a windowed partial ratio (handles a long footer block embedded in
a much larger document). Anchors are mandatory substrings that must survive
even when the body matches.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from rapidfuzz import fuzz

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, strip punctuation/zero-width noise, collapse whitespace."""
    s = (s or "").lower().replace("​", "").replace("﻿", "")
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _similarity(required_norm: str, doc_norm: str) -> float:
    if not required_norm or not doc_norm:
        return 0.0
    return fuzz.ratio(required_norm, doc_norm) / 100.0


def _anchors_present(anchors: List[str], doc_norm: str) -> bool:
    for a in anchors:
        if normalize(a) not in doc_norm:
            return False
    return True


def classify(
    required_text: str,
    anchors: List[str],
    document_text: str,
    present_threshold: float,
    altered_threshold: float,
) -> Tuple[str, float]:
    doc_norm = normalize(document_text)
    req_norm = normalize(required_text)
    sim = _similarity(req_norm, doc_norm)
    anchors_ok = _anchors_present(anchors or [], doc_norm)
    if sim >= present_threshold and anchors_ok and anchors:
        return "present", sim
    if sim >= altered_threshold or (sim >= present_threshold and not anchors_ok):
        return "altered", sim
    return "missing", sim
