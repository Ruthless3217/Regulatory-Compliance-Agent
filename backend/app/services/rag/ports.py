"""Protocols (interfaces) for the swappable RAG backends.

These let dispatch_node, retrievers, and indexers depend on a contract,
not a concrete pgvector or Azure implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------- types ----

IndexName = Literal[
    "rag_rules", "rag_chunks", "rag_source_docs", "rag_compliance_examples",
    "rag_product_docs", "precedent_cases",
]


@dataclass
class VectorDoc:
    """A single row destined for any of the four indexes.

    `id` and `embedding` are required; everything else lives in `fields`
    and is interpreted per-index by the store implementation.
    """
    id: str
    embedding: List[float]
    fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    id: str
    score: float
    fields: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------- embedder ----

@runtime_checkable
class Embedder(Protocol):
    """A text-to-vector embedder. Implementations: OpenAI, Azure OpenAI."""

    model: str
    dim: int

    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        """Return one vector per input text. Order preserved.

        ``input_type`` is 'search_document' for indexed content and
        'search_query' for retrieval queries. Asymmetric models (Cohere v3)
        embed the two differently; symmetric models (OpenAI/Azure) ignore it.
        """
        ...


# ------------------------------------------------------- vector store ----

@runtime_checkable
class VectorStore(Protocol):
    """Backend for indexing + retrieving vectors.

    The same instance handles all four logical indexes; methods take the
    index name as a parameter so we don't fan out to three clients.
    """

    name: str   # e.g. "pgvector" | "azure_search"

    async def upsert(self, index: IndexName, docs: List[VectorDoc]) -> None:
        ...

    async def delete(self, index: IndexName, ids: List[str]) -> None:
        ...

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
        """Vector + BM25 hybrid search with RRF fusion.

        `filters` is a dict of field -> value (or list of values for IN).
        Implementations translate to backend-native filter syntax.
        """
        ...

    async def vector_search(
        self,
        index: IndexName,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        """Pure vector cosine-similarity search (no BM25)."""
        ...

    async def health(self) -> bool:
        """Return True if the backend is reachable."""
        ...
