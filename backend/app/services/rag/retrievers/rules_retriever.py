"""Per-chunk rule retrieval — the analysis-time RAG entry point.

For each submission chunk, returns the top-K most relevant rules per
category (filtered to `is_active=true`). Output shape matches what
analysis_node expects: nested dict keyed first by chunk_id then by category.

Concurrency: launches all (chunk × category) queries in parallel via
`asyncio.gather`. Typical 6-chunk × 3-category submission ≈ 18 queries
in <300ms on either backend.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit

logger = logging.getLogger(__name__)


def _hit_to_rule_dict(hit: SearchHit) -> Dict[str, Any]:
    """Mirror the shape that analysis_node already expects from rules."""
    f = hit.fields
    return {
        "id": hit.id,
        "rule_text": f.get("rule_text", ""),
        "category": f.get("category", ""),
        "severity": f.get("severity", "medium"),
        "keywords": f.get("keywords") or [],
        "rag_score": hit.score,
        "source": f.get("source"),
    }


class RulesRetriever:
    """Retrieves the most relevant rules per chunk and category."""

    async def retrieve_per_chunk(
        self,
        chunks: List[Dict[str, Any]],
        categories: List[str],
        top_k: int,
        recall_pool: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """
        Returns:
            { chunk_id: { category: [rule_dict, ...] } }
        """
        if not chunks or not categories:
            return {}

        recall = recall_pool if recall_pool is not None else settings.rag_recall_pool
        threshold = (
            score_threshold if score_threshold is not None else settings.rag_score_threshold
        )

        embedder = get_embedder()
        store = get_vector_store()

        # 1. One embed call for all chunks at once.
        try:
            chunk_texts = [c.get("text", "") for c in chunks]
            chunk_vectors = await embedder.embed(chunk_texts, input_type="search_query")
        except RAGEmbedFailed as e:
            logger.warning(f"Embedder unavailable in rules retriever: {e}")
            raise RAGDegraded(str(e)) from e

        # 2. Parallel hybrid_search across (chunk, category).
        async def one_query(chunk_idx: int, category: str):
            try:
                hits = await store.hybrid_search(
                    index="rag_rules",
                    query_text=chunk_texts[chunk_idx][:2000],
                    query_vector=chunk_vectors[chunk_idx],
                    top_k=top_k,
                    recall_pool=recall,
                    rrf_k=settings.rag_rrf_k,
                    filters={"category": category, "is_active": True},
                )
                hits = [h for h in hits if h.score >= threshold]
                return chunks[chunk_idx]["id"], category, hits
            except RAGDegraded:
                raise
            except Exception as e:
                logger.error(
                    f"rules retriever query failed (chunk={chunk_idx}, cat={category}): {e}"
                )
                raise RAGDegraded(str(e)) from e

        tasks = [
            one_query(i, cat) for i in range(len(chunks)) for cat in categories
        ]
        results = await asyncio.gather(*tasks)

        # 3. Re-bucket into nested dict.
        out: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for chunk_id, category, hits in results:
            out.setdefault(chunk_id, {}).setdefault(category, [])
            out[chunk_id][category] = [_hit_to_rule_dict(h) for h in hits]
        return out


_retriever_singleton: Optional[RulesRetriever] = None


def get_rules_retriever() -> RulesRetriever:
    global _retriever_singleton
    if _retriever_singleton is None:
        _retriever_singleton = RulesRetriever()
    return _retriever_singleton
