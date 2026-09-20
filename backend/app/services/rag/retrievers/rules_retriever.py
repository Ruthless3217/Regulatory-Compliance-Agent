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
from app.services.rag import trace as rag_trace
from app.services.observability.tracing import observe, update_span

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

    @observe(name="retrieve-rules", as_type="retriever", capture_input=False, capture_output=False)
    async def retrieve_per_chunk(
        self,
        chunks: List[Dict[str, Any]],
        categories: List[str],
        top_k: int,
        recall_pool: Optional[int] = None,
        score_threshold: Optional[float] = None,
        product_scope: Optional[List[Optional[str]]] = None,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """
        Returns:
            { chunk_id: { category: [rule_dict, ...] } }

        `product_scope` (applicability.scope_filter_values) pushes the product
        cut into SQL so out-of-scope rules stop consuming recall-pool slots.
        It always contains None, so globally/untagged rules stay retrievable;
        None for the whole argument means "no product resolved" -> no filter.
        """
        if not chunks or not categories:
            return {}

        recall = recall_pool if recall_pool is not None else settings.rag_recall_pool
        update_span(input={
            "chunks": len(chunks), "categories": list(categories), "top_k": top_k,
            "recall_pool": recall, "product_scope": product_scope,
        })
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

        base_filters: Dict[str, Any] = {"is_active": True}
        if product_scope:
            base_filters["product_line"] = product_scope

        # 2. Parallel hybrid_search across (chunk, category).
        async def one_query(chunk_idx: int, category: str):
            try:
                # Label the query so the retrieval trace can attribute each
                # candidate's cosine/ts_rank to the chunk that asked — the same
                # rule scores differently against every chunk.
                with rag_trace.query(
                    chunk_id=str(chunks[chunk_idx]["id"]), category=category
                ):
                    hits = await store.hybrid_search(
                        index="rag_rules",
                        query_text=chunk_texts[chunk_idx][:2000],
                        query_vector=chunk_vectors[chunk_idx],
                        top_k=top_k,
                        recall_pool=recall,
                        rrf_k=settings.rag_rrf_k,
                        filters={**base_filters, "category": category},
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
        # Rule ids per chunk/category (the full rule text reaches the model and
        # is visible on the grading generation's input).
        update_span(output={
            "chunks": len(out),
            "rules_per_chunk": {
                cid: {cat: [str(r.get("id") or r.get("rule_id")) for r in rules] for cat, rules in cats.items()}
                for cid, cats in list(out.items())[:50]
            },
        })
        return out


_retriever_singleton: Optional[RulesRetriever] = None


def get_rules_retriever() -> RulesRetriever:
    global _retriever_singleton
    if _retriever_singleton is None:
        _retriever_singleton = RulesRetriever()
    return _retriever_singleton
