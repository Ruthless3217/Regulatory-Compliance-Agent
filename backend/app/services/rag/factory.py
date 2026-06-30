"""Singleton factory — selects concrete Embedder + VectorStore from settings.

Callers always go through `get_embedder()` and `get_vector_store()`; they
never import a concrete class. Swapping pgvector ↔ Azure AI Search is a
single env-var change with no code edits.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.services.rag.errors import RAGDegraded
from app.services.rag.ports import Embedder, VectorStore

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    provider = (settings.rag_embedding_provider or "azure_cohere").lower()
    if provider == "azure_cohere":
        from app.services.rag.embedders.azure_cohere_embedder import AzureCohereEmbedder
        logger.info("RAG embedder: AzureCohereEmbedder")
        return AzureCohereEmbedder()
    raise RAGDegraded(
        f"RAG_EMBEDDING_PROVIDER '{provider}' is disabled/removed. "
        "Only 'azure_cohere' (Cohere-embed-v3-multilingual on Azure AI Foundry) is supported."
    )


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    backend = (settings.rag_vector_backend or "pgvector").lower()
    if backend == "azure_search":
        from app.services.rag.stores.azure_search_store import AzureSearchStore
        logger.info("RAG vector store: AzureSearchStore")
        return AzureSearchStore()
    if backend == "pgvector":
        from app.services.rag.stores.pgvector_store import PgVectorStore
        logger.info("RAG vector store: PgVectorStore")
        return PgVectorStore()
    raise RAGDegraded(f"Unknown RAG_VECTOR_BACKEND: {backend}")


def reset_singletons() -> None:
    """Test helper — clears cached instances so env-var changes take effect."""
    get_embedder.cache_clear()
    get_vector_store.cache_clear()
