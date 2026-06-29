"""Precedent retriever — per-chunk hybrid lookup against precedent_cases.

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
from app.services.rag.precedent_filters import is_thin_comment

logger = logging.getLogger(__name__)


def _is_thin(precedent: Dict[str, Any]) -> bool:
    return is_thin_comment(precedent.get("comment_text"))


def _hit_to_precedent(hit: SearchHit) -> Dict[str, Any]:
    f = hit.fields or {}
    return {
        "id": hit.id,
        "score": hit.score,
        # Downstream-stable keys (graph/nodes.py) mapped from precedent_cases.
        "reviewer_name": f.get("reviewer_role"),
        "comment_text": f.get("reviewer_comment"),
        "chunk_text": f.get("span_context"),
        "anchor_text": f.get("highlighted_span"),
        "final_text_chunk": f.get("after_text"),
        "violation_category": f.get("issue_type"),
        "severity": f.get("severity"),
        "document_id": f.get("ticket"),
        "source_file": f.get("source_file"),
        # New context fields the prompt/UI can use for the "why".
        "why_rationale": f.get("why_rationale"),
        "guideline_ref": f.get("guideline_ref"),
        "occurrence_count": f.get("occurrence_count"),
    }


def _hit_to_precedent_legacy(hit: SearchHit) -> Dict[str, Any]:
    """Map a rag_compliance_examples hit to the same downstream-stable keys.

    Column names in rag_compliance_examples differ from precedent_cases, so
    this mapping bridges the gap when the v2 table hasn't been populated yet.
    """
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
        "why_rationale": None,
        "guideline_ref": None,
        "occurrence_count": None,
    }


# Cache: None = not checked, True = has rows, False = empty.
_precedent_cases_populated: Optional[bool] = None


def _check_precedent_cases_populated() -> bool:
    """Check once per process whether precedent_cases has any rows."""
    global _precedent_cases_populated
    if _precedent_cases_populated is not None:
        return _precedent_cases_populated
    try:
        from sqlalchemy import text as sa_text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            row = db.execute(sa_text("SELECT EXISTS(SELECT 1 FROM precedent_cases LIMIT 1)")).scalar()
            _precedent_cases_populated = bool(row)
        finally:
            db.close()
    except Exception:
        _precedent_cases_populated = False
    logger.info(
        "precedent_cases populated: %s (will %s rag_compliance_examples)",
        _precedent_cases_populated,
        "NOT fall back to" if _precedent_cases_populated else "fall back to",
    )
    return _precedent_cases_populated


class PrecedentRetriever:
    async def retrieve_per_chunk(
        self,
        chunks: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        exclude_document_id: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """For each chunk, return its top_k most-similar precedents.
        On embed/store failure for a chunk, that chunk yields [].

        Falls back to the legacy rag_compliance_examples table when
        precedent_cases is empty (v2 ingestion not yet run).
        """
        if not chunks:
            return {}

        # Decide which index + mapper to use.
        use_v2 = _check_precedent_cases_populated()
        index = "precedent_cases" if use_v2 else "rag_compliance_examples"
        mapper = _hit_to_precedent if use_v2 else _hit_to_precedent_legacy

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
                    index=index,
                    query_text=chunk.get("text", ""),
                    query_vector=qvec,
                    top_k=k,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                    filters=None,
                )
                precedents = [mapper(h) for h in hits]
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

