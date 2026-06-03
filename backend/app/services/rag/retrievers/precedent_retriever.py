"""Precedent retriever — per-chunk hybrid lookup against rag_compliance_examples.

Returns { chunk_id: [precedent_dict, ...] }. Used by dispatch_node to attach
state.retrieved_examples for the precedent analysis path.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit

logger = logging.getLogger(__name__)


# Pure-response reviewer comments carry no compliance signal — they're the
# reviewer accepting/closing a thread, not flagging an issue. Mirror of the
# 0007 corpus-purge denylist; this runtime guard repeats the filter so a bad
# future ingest can't reintroduce thin rows into analysis.
_RESPONSE_TOKENS = frozenset({
    "done", "ok", "okay", "yes", "no",
    "noted", "agreed", "agree", "fine", "accepted", "approved", "confirmed",
    "added", "edited", "deleted", "revised", "rephrased", "checked", "check",
})


def _is_thin(precedent: Dict[str, Any]) -> bool:
    """True when a precedent's comment carries no actionable signal — either a
    pure-response token (done/ok/added/…) or shorter than 3 chars after
    stripping trailing punctuation. Substantive short flags ('source?',
    'rephrase.') are preserved."""
    c = (precedent.get("comment_text") or "").strip().lower().rstrip(".!?")
    return c in _RESPONSE_TOKENS or len(c) < 3


def _hit_to_precedent(hit: SearchHit) -> Dict[str, Any]:
    f = hit.fields or {}
    return {
        "id": hit.id,
        "score": hit.score,
        "reviewer_name": f.get("reviewer_name"),
        "comment_text": f.get("comment_text"),
        "chunk_text": f.get("chunk_text"),
        "anchor_text": f.get("anchor_text"),
        "final_text_chunk": f.get("final_text_chunk"),
        "violation_category": f.get("violation_category"),
        "severity": f.get("severity"),
        "document_id": f.get("document_id"),
        "source_file": f.get("source_file"),
    }


class PrecedentRetriever:
    async def retrieve_per_chunk(
        self,
        chunks: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        exclude_document_id: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """For each chunk, return its top_k most-similar precedents.
        On embed/store failure for a chunk, that chunk yields []."""
        if not chunks:
            return {}
        k = top_k or settings.pgvector_top_k
        embedder = get_embedder()
        store = get_vector_store()

        texts = [c.get("text", "") for c in chunks]
        try:
            vectors = await embedder.embed(texts, input_type="search_query")
        except (RAGEmbedFailed, RAGDegraded) as e:
            logger.warning(f"precedent retrieval embed failed: {e}")
            return {str(c.get("id")): [] for c in chunks}

        if len(vectors) != len(chunks):
            logger.error(
                "precedent retrieval: embedder returned %d vectors for %d chunks; "
                "returning empty results",
                len(vectors),
                len(chunks),
            )
            return {str(c.get("id")): [] for c in chunks}

        out: Dict[str, List[Dict[str, Any]]] = {}
        for chunk, qvec in zip(chunks, vectors):
            cid = str(chunk.get("id"))
            try:
                hits = await store.hybrid_search(
                    index="rag_compliance_examples",
                    query_text=chunk.get("text", ""),
                    query_vector=qvec,
                    top_k=k,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                    filters=None,
                )
                precedents = [_hit_to_precedent(h) for h in hits]
                # Runtime safety net (mirrors the 0007 corpus purge): drop
                # pure-response precedents the store may still surface. Idempotent
                # with the migration; protects against bad future ingests.
                precedents = [p for p in precedents if not _is_thin(p)]
                # Leakage guard for the eval harness: drop same-document precedents.
                # (Done in Python, not via store filters, because the store's filter
                # semantics are equality-inclusion — they can't express "not equal".)
                if exclude_document_id:
                    precedents = [
                        p
                        for p in precedents
                        if p.get("document_id") is None
                        or str(p.get("document_id")) != str(exclude_document_id)
                    ]
                out[cid] = precedents
            except RAGDegraded as e:
                logger.warning(f"precedent retrieval degraded for chunk {cid}: {e}")
                out[cid] = []
        return out


_singleton: Optional[PrecedentRetriever] = None


def get_precedent_retriever() -> PrecedentRetriever:
    global _singleton
    if _singleton is None:
        _singleton = PrecedentRetriever()
    return _singleton
