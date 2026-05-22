"""RAG-layer exceptions.

Distinguishes retrieval/embed degradation (fall back gracefully) from
indexing failure (log, keep DB write, backfill catches it).
"""


class RAGError(Exception):
    """Base RAG exception."""


class RAGDegraded(RAGError):
    """Vector store or embedder unreachable. Caller should fall back."""


class RAGEmbedFailed(RAGDegraded):
    """Embedder returned non-200 or malformed output."""


class RAGIndexingFailed(RAGError):
    """Upsert into vector store failed. DB write must NOT be rolled back."""
