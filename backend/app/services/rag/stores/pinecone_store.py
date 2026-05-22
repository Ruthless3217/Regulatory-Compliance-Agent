"""Pinecone vector store (alternative v1 backend, dense-only).

Speaks the VectorStore protocol. One Pinecone index, three namespaces
(rag_rules, rag_chunks, rag_source_docs). No BM25 — hybrid_search() is an
alias for vector_search(). Pinecone SDK is imported lazily so installs
without RAG_VECTOR_BACKEND=pinecone don't pay the import cost.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.ports import IndexName, SearchHit, VectorDoc

logger = logging.getLogger(__name__)


# ----------------------------------------------------------- helpers ---

def _coerce_scalar(v: Any) -> Any:
    """Pinecone metadata values: str/int/float/bool/list[str]. Coerce UUIDs."""
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _to_pinecone_filter(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Translate internal filter dict to Pinecone's filter DSL.

    Internal shape: {field: value} or {field: [v1, v2, ...]}.
    Pinecone shape: {field: {"$eq": value}} or {field: {"$in": [v1, v2]}}.
    """
    if not filters:
        return None
    out: Dict[str, Any] = {}
    for k, v in filters.items():
        if isinstance(v, (list, tuple, set)):
            out[k] = {"$in": [_coerce_scalar(x) for x in v]}
        else:
            out[k] = {"$eq": _coerce_scalar(v)}
    return out


# Truncation caps (bytes) — keeps each vector's metadata under Pinecone's
# 40 KB/vector limit while reserving headroom for the other fields.
_TEXT_TRUNCATE = 32 * 1024     # rag_chunks.text, rag_source_docs.text
_RULE_TEXT_TRUNCATE = 8 * 1024  # rag_rules.rule_text


def _sanitize_metadata(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare a VectorDoc.fields dict for Pinecone's metadata field.

    - Drop None values (Pinecone rejects them).
    - Coerce UUIDs (and lists of UUIDs) to strings.
    - Truncate long text fields under Pinecone's 40 KB/vector cap.
    - Pass through str/int/float/bool/list[str] unchanged.
    """
    out: Dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
            continue
        if isinstance(v, (list, tuple)):
            out[k] = [str(x) if isinstance(x, uuid.UUID) else x for x in v]
            continue
        if isinstance(v, str):
            cap = _RULE_TEXT_TRUNCATE if k == "rule_text" else _TEXT_TRUNCATE
            out[k] = v[:cap] if len(v) > cap else v
            continue
        out[k] = v
    return out


# Map IndexName -> namespace setting attribute on settings.
def _namespace_for(index: IndexName) -> str:
    from app.config import settings
    if index == "rag_rules":
        return settings.pinecone_namespace_rules
    if index == "rag_chunks":
        return settings.pinecone_namespace_chunks
    if index == "rag_source_docs":
        return settings.pinecone_namespace_srcdocs
    raise ValueError(f"Unknown index: {index}")


# Stable ID prefixes — guard against accidental cross-namespace delete.
_ID_PREFIX: Dict[str, str] = {
    "rag_rules": "rules",
    "rag_chunks": "chunks",
    "rag_source_docs": "srcdocs",
}


def _prefix_id(index: IndexName, raw_id: str) -> str:
    return f"{_ID_PREFIX[index]}:{raw_id}"


# ========================================================== PineconeStore

class PineconeStore:
    """VectorStore implementation backed by Pinecone (dense-only)."""

    name = "pinecone"

    def __init__(self) -> None:
        from app.config import settings

        if not settings.pinecone_api_key or not settings.pinecone_index_name:
            raise RAGDegraded(
                "Pinecone backend selected but PINECONE_API_KEY or "
                "PINECONE_INDEX_NAME is not set"
            )

        # Lazy import so the SDK is only required when this backend is active.
        try:
            from pinecone import Pinecone  # type: ignore
        except ImportError as e:
            raise RAGDegraded(f"pinecone SDK not installed: {e}") from e

        try:
            self._pc = Pinecone(api_key=settings.pinecone_api_key)
            self._index = self._pc.Index(settings.pinecone_index_name)
        except Exception as e:
            raise RAGDegraded(f"failed to connect to Pinecone index: {e}") from e

        self._dim = settings.rag_embedding_dim
        self._hybrid_warned = False

    async def _run_sync(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def health(self) -> bool:
        def _do() -> bool:
            try:
                self._index.query(
                    namespace=_namespace_for("rag_rules"),
                    vector=[0.0] * self._dim,
                    top_k=1,
                )
                return True
            except Exception as e:
                logger.warning(f"Pinecone health probe failed: {e}")
                return False

        return await self._run_sync(_do)

    # ------------------ upsert / delete ------------------

    _UPSERT_BATCH = 100

    async def upsert(self, index: IndexName, docs: List[VectorDoc]) -> None:
        if not docs:
            return
        ns = _namespace_for(index)
        vectors = []
        for d in docs:
            if len(d.embedding) != self._dim:
                raise RAGIndexingFailed(
                    f"Pinecone upsert {index}: embedding dim {len(d.embedding)} != {self._dim}"
                )
            vectors.append({
                "id": _prefix_id(index, d.id),
                "values": d.embedding,
                "metadata": _sanitize_metadata(d.fields),
            })

        def _do():
            try:
                for i in range(0, len(vectors), self._UPSERT_BATCH):
                    batch = vectors[i : i + self._UPSERT_BATCH]
                    self._index.upsert(vectors=batch, namespace=ns)
            except Exception as e:
                raise RAGIndexingFailed(
                    f"Pinecone upsert into {index} (ns={ns}) failed: {e}"
                ) from e

        await self._run_sync(_do)

    async def delete(self, index: IndexName, ids: List[str]) -> None:
        if not ids:
            return
        ns = _namespace_for(index)
        prefixed = [_prefix_id(index, i) for i in ids]

        def _do():
            try:
                self._index.delete(ids=prefixed, namespace=ns)
            except Exception as e:
                raise RAGIndexingFailed(
                    f"Pinecone delete from {index} (ns={ns}) failed: {e}"
                ) from e

        await self._run_sync(_do)

    # ------------------ search ------------------

    async def vector_search(
        self,
        index: IndexName,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        ns = _namespace_for(index)
        pc_filter = _to_pinecone_filter(filters)
        prefix = _ID_PREFIX[index] + ":"

        def _do() -> List[SearchHit]:
            try:
                res = self._index.query(
                    namespace=ns,
                    vector=query_vector,
                    top_k=top_k,
                    include_metadata=True,
                    filter=pc_filter,
                )
            except Exception as e:
                logger.error(f"Pinecone vector_search on {index} failed: {e}")
                raise RAGDegraded(str(e)) from e

            hits: List[SearchHit] = []
            for m in res.matches or []:
                # Strip the namespace prefix from the returned ID so callers
                # get the raw UUID they upserted.
                raw_id = m.id[len(prefix):] if m.id.startswith(prefix) else m.id
                meta = dict(m.metadata or {})
                hits.append(SearchHit(id=raw_id, score=float(m.score), fields=meta))
            return hits

        return await self._run_sync(_do)

    async def hybrid_search(
        self,
        index: IndexName,
        query_text: str,
        query_vector: List[float],
        top_k: int,
        recall_pool: int,
        rrf_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        # Pinecone has no BM25 leg. query_text / recall_pool / rrf_k are
        # accepted for protocol parity and ignored.
        if not self._hybrid_warned:
            logger.info("Pinecone backend: hybrid_search() is vector-only (BM25 disabled)")
            self._hybrid_warned = True
        return await self.vector_search(index, query_vector, top_k, filters)
