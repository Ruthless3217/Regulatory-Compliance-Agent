"""Precedent retriever — per-chunk hybrid lookup against precedent_cases.

Returns { chunk_id: [precedent_dict, ...] }. Used by dispatch_node to attach
state.retrieved_examples for the precedent analysis path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit
from app.services.rag.precedent_filters import is_thin_comment
from app.services.rag import trace as rag_trace

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
        # Scope tag consumed by rag.applicability — was previously dropped here,
        # which made product-aware validation impossible (RETRIEVAL_RCA.md §1).
        "product_category": f.get("product_category"),
        # Provenance, not polarity: True means the row was authored from
        # reviewer feedback (rule_feedback_service._reviewer_precedent_row),
        # which covers CONFIRMED findings and rejections alike. It was dropped
        # here before, so a consumer could not tell a taught precedent from an
        # ingested one. `preprocessing_service._is_reviewer_rejection` still
        # decides direction from the issue_type prefix — see its docstring.
        "is_reviewer": bool(f.get("is_reviewer")),
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
        # The legacy table has no such column; keep the key so consumers can
        # read it unconditionally.
        "is_reviewer": False,
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
        product_scope: Optional[List[Optional[str]]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """For each chunk, return its top_k most-similar precedents.
        On embed/store failure for a chunk, that chunk yields [].

        Falls back to the legacy rag_compliance_examples table when
        precedent_cases is empty (v2 ingestion not yet run).

        `product_scope` (applicability.scope_filter_values) is pushed into the
        `product_category` predicate so a ULIP precedent never occupies a
        recall-pool slot on a term submission. It carries every spelling the
        ingest heuristic emits ('ULIP', 'Non-Par', ...) plus global,
        cross-cutting and None. The legacy table has no scope column, so the
        fallback path stays unfiltered and relies on the post-retrieval judge.
        """
        if not chunks:
            return {}

        # Decide which index + mapper to use.
        use_v2 = _check_precedent_cases_populated()
        index = "precedent_cases" if use_v2 else "rag_compliance_examples"
        mapper = _hit_to_precedent if use_v2 else _hit_to_precedent_legacy
        filters = (
            {"product_category": product_scope} if (use_v2 and product_scope) else None
        )

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

        # Parallel hybrid_search across chunks (mirrors RulesRetriever's
        # per-(chunk, category) gather — each chunk's query is independent,
        # so N sequential store round-trips would just be latency for no
        # benefit). Each task resolves to (cid, precedents) with a per-chunk
        # RAGDegraded->[] fallback, never propagating, so gather can't abort.
        async def one_query(chunk: Dict[str, Any], qvec: List[float]) -> tuple:
            cid = str(chunk.get("id"))
            try:
                # Label the query so the retrieval trace can attribute each
                # candidate's leg scores to the chunk that asked.
                with rag_trace.query(chunk_id=cid):
                    hits = await store.hybrid_search(
                        index=index,
                        query_text=chunk.get("text", ""),
                        query_vector=qvec,
                        top_k=k,
                        recall_pool=settings.rag_recall_pool,
                        rrf_k=settings.rag_rrf_k,
                        filters=filters,
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
                return cid, precedents
            except RAGDegraded as e:
                logger.warning(f"precedent retrieval degraded for chunk {cid}: {e}")
                return cid, []

        tasks = [one_query(chunk, qvec) for chunk, qvec in zip(chunks, vectors)]
        results = await asyncio.gather(*tasks)

        out: Dict[str, List[Dict[str, Any]]] = {}
        for cid, precedents in results:
            out[cid] = precedents
        return out


_singleton: Optional[PrecedentRetriever] = None


def get_precedent_retriever() -> PrecedentRetriever:
    global _singleton
    if _singleton is None:
        _singleton = PrecedentRetriever()
    return _singleton

