"""Document-wide verbatim matching for mandated disclaimers.

Normalizes both sides, then picks the similarity metric by length: when the
document is at least as long as the required text (the common case — a small
footer disclaimer embedded in a much larger marketing document), a windowed
partial ratio locates the best-matching span; when the document is a fragment
shorter than the required text, a direct ratio is used instead. Anchors are
mandatory substrings that must survive even when the body matches.
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
    if len(doc_norm) >= len(required_norm):
        # Large-doc path: locate the disclaimer window inside the document.
        return fuzz.partial_ratio(required_norm, doc_norm) / 100.0
    # Doc is a fragment shorter than the required text — use direct ratio.
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
    if sim >= present_threshold and anchors_ok:
        return "present", sim
    if sim >= altered_threshold or (sim >= present_threshold and not anchors_ok):
        return "altered", sim
    return "missing", sim
