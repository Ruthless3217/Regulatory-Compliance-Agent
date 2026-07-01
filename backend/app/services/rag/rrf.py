"""Reciprocal Rank Fusion — combine multiple ranked lists into one.

Used by the pgvector backend to fuse the vector-similarity leg and the
BM25 leg. Azure AI Search does this natively, so the Azure store doesn't
import this module.
"""
from typing import Dict, Iterable, List, Tuple


def reciprocal_rank_fusion(
 ranked_lists: Iterable[List[Tuple[str, float]]],
 k: int = 60,
) -> List[Tuple[str, float]]:
 """Fuse N ranked lists into one.

 Each input list is [(id, score), ...] ordered best-first. The score is
 only used as a tiebreaker for identical ranks within one list; cross-list
 fusion uses ranks via 1 / (k + rank).

 Returns combined [(id, fused_score), ...] sorted descending.
 """
 fused: Dict[str, float] = {}
 for ranked in ranked_lists:
 for rank, (doc_id, _score) in enumerate(ranked):
 fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
 return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
