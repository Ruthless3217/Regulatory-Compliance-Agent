"""Precedent retriever — per-chunk hybrid lookup against rag_compliance_examples.

Returns { chunk_id: [precedent_dict, ...] }. Used by dispatch_node to attach
state.retrieved_examples for the precedent analysis path.
"""
from __future__ import annotations

import logging
import re
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

# High-frequency reviewer chatter that carries no compliance signal *as a
# precedent example* (KB/rules quality audit, 2026-06-02). Unlike the 0007
# response tokens these are multi-word, so they're matched after normalization
# (lowercase, collapsed whitespace, stripped surrounding punctuation/quotes).
# Suppressed at retrieval only — the rows stay in the corpus for audit, and
# `_RESPONSE_TOKENS` stays untouched so the 0007 sync guard holds.
#
# Buckets are kept explicit so the kill-list is reviewable. Deliberately NOT
# included: anything disclaimer-related ('ulip disclaimer', 'ref sign and
# disclaimer'), source-contradiction flags ('not there in source'), and terse
# but real issues ('incorrect') — those are genuine signal.
_NOISE_STATUS = {
    "new content", "new content added", "newly added", "changed", "corrected",
    "removed", "updated", "same comment as above",
    "this is already approved hence not rephrasing",
}
_NOISE_EDITORIAL = {
    "rephrase", "pls rephrase", "rephrase this", "pls rephrase this",
    "what do we mean by this", "what does this mean", "what is this", "what",
    "how", "pls elaborate", "pls elaborate how", "grammar check", "full form",
}
_NOISE_ROUTING = {
    "tax team to vet", "tax team approval",
    "pls take this ahead basis marketing pd approval",
    "pls take this ahead basis tax team approval",
}
# Generic source-request cluster. The rule tier now grounds 'claims need
# sources' with a real regulatory quote, so these contentless asks are
# redundant as few-shot precedents.
_NOISE_SOURCE_REQUEST = {
    "source", "request source", "request source link", "pls add source",
    "pls add source link", "pls give us source", "pls attach source link",
    "attach source link", "pls help us locate", "pls help us locate in source link",
    "pls help us locate this in the source link", "pls help us locate this in source link",
    "pls help us locate in the source link", "pls align basis source link",
    "made changes basis source link", "where have we taken this from",
    "where is this in the source link",
}
_NOISE_PHRASES = frozenset(
    _NOISE_STATUS | _NOISE_EDITORIAL | _NOISE_ROUTING | _NOISE_SOURCE_REQUEST
)

_WS_RE = re.compile(r"\s+")
# Surrounding punctuation/quotes to strip before phrase comparison (incl. the
# curly apostrophe ’ that appears in the real corpus).
_STRIP_CHARS = " .,!?;:\"'’"


def _normalize_comment(text: Optional[str]) -> str:
    c = _WS_RE.sub(" ", (text or "").strip().lower())
    return c.strip(_STRIP_CHARS)


def _is_thin(precedent: Dict[str, Any]) -> bool:
    """True when a precedent's comment carries no actionable signal as a
    few-shot example: a pure-response token (done/ok/added/…), shorter than 3
    chars, or high-frequency reviewer chatter (status/editorial/routing/generic
    source-request). Substantive short flags ('ulip disclaimer', 'not there in
    source', 'incorrect') are preserved."""
    raw = (precedent.get("comment_text") or "").strip().lower().rstrip(".!?")
    if raw in _RESPONSE_TOKENS or len(raw) < 3:
        return True
    return _normalize_comment(precedent.get("comment_text")) in _NOISE_PHRASES


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
