"""Product-docs retriever — hybrid lookup against rag_product_docs.

The rag_product_docs corpus (Brochure Phase 1) is indexed by
``product_docs_indexer`` but, until now, had no retriever — so the
product reference material (eligibility, charges, mandatory descriptors,
disclaimers) never reached chat or analysis. This retriever closes that
gap: it answers "what does the approved brochure say / how should this be
worded for THIS product" with self-contained, heading-prefixed passages.

Fail-soft by design: product docs are advisory grounding, not the grading
gate, so a degraded retrieval returns ``[]`` rather than failing the run
(the precedent/rule fail-closed guards remain the gating path).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit

logger = logging.getLogger(__name__)

try:
 from langsmith import traceable
except Exception: # pragma: no cover
 def traceable(*_a, **_kw): # type: ignore
 def _d(fn): return fn
 return _d if not (_a and callable(_a[0])) else _a[0]


def _hit_to_product_doc(hit: SearchHit) -> Dict[str, Any]:
 f = hit.fields or {}
 return {
 "id": hit.id,
 "score": hit.score,
 "product_document_id": f.get("product_document_id"),
 "uin": f.get("uin"),
 "product_name": f.get("product_name"),
 "page_number": f.get("page_number"),
 "section_path": f.get("section_path"),
 "block_type": f.get("block_type"),
 "text": f.get("text"),
 }


class ProductDocsRetriever:
 """Retrieves the most relevant approved-brochure passages for a query,
 optionally scoped to a product (``uin``/``product_name``) or block type
 (e.g. ``disclosure`` for disclaimer wording)."""

 @traceable(run_type="retriever", name="RAG.product_docs_retriever")
 async def retrieve(
 self,
 query: str,
 top_k: Optional[int] = None,
 uin: Optional[str] = None,
 product_name: Optional[str] = None,
 block_type: Optional[str] = None,
 ) -> List[Dict[str, Any]]:
 """Return up to ``top_k`` product-doc passages for ``query``.

 Returns ``[]`` (fail-soft) when the embedder or store is unavailable —
 product docs enrich but never gate a compliance grade.
 """
 if not (query and query.strip()):
 return []
 k = top_k or settings.rag_top_k_chat
 embedder = get_embedder()
 store = get_vector_store()

 try:
 qvec = (await embedder.embed([query], input_type="search_query"))[0]
 except (RAGEmbedFailed, RAGDegraded) as e:
 logger.warning(f"product-docs retrieval embed failed: {e}")
 return []

 # Build an equality filter only for the fields the caller scoped.
 filters: Dict[str, Any] = {}
 if uin:
 filters["uin"] = uin
 if product_name:
 filters["product_name"] = product_name
 if block_type:
 filters["block_type"] = block_type

 try:
 hits = await store.hybrid_search(
 index="rag_product_docs",
 query_text=query,
 query_vector=qvec,
 top_k=k,
 recall_pool=settings.rag_recall_pool,
 rrf_k=settings.rag_rrf_k,
 filters=filters or None,
 )
 except RAGDegraded as e:
 logger.warning(f"product-docs retrieval degraded: {e}")
 return []
 return [_hit_to_product_doc(h) for h in hits]


_singleton: Optional[ProductDocsRetriever] = None


def get_product_docs_retriever() -> ProductDocsRetriever:
 global _singleton
 if _singleton is None:
 _singleton = ProductDocsRetriever()
 return _singleton
