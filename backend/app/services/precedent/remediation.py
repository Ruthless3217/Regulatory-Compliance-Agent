"""Link a precedent's highlighted span to the reviewer's approved rewrite.

remediation_pairs are paragraph-level and noisy (see docs/ledger/README.md), so
this attaches a *candidate* fix (surfaced as "suggested"), not ground truth.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz


def link_remediation(
 span: str, pairs: List[Dict[str, str]], min_fuzzy: int = 60
) -> Tuple[Optional[str], Optional[str]]:
 span = (span or "").strip()
 if not span or not pairs:
 return None, None
 needle = span[:100]
 best_idx, best_score = None, 0
 for idx, pair in enumerate(pairs):
 before = (pair.get("before") or "")
 if not before:
 continue
 if span in before:
 return before, (pair.get("after") or None)
 score = int(fuzz.partial_ratio(needle, before[:400]))
 if score > best_score:
 best_idx, best_score = idx, score
 if best_idx is not None and best_score >= min_fuzzy:
 p = pairs[best_idx]
 return p.get("before"), (p.get("after") or None)
 return None, None
