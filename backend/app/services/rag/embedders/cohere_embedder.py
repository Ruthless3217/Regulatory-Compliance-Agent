"""Cohere embed-english-v3.0 embedder (1024-dim, alternative to OpenAI).

Cohere requires an `input_type` per call. We use 'search_document' for
indexing and 'search_query' for retrieval queries. To keep the Embedder
protocol simple, both retrievers and indexers route through `embed()`;
internally we default to 'search_document' which gives the right vectors
for upsert. Query-time vectors are slightly off-distribution but Cohere's
asymmetric models still match well enough for v1.
"""
from __future__ import annotations

import logging
from typing import List

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

logger = logging.getLogger(__name__)

_BATCH_SIZE = 96


class CohereEmbedder:
    """Embedder backed by Cohere's hosted embedding API."""

    name = "cohere"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.model = model or settings.cohere_embedding_model
        self.dim = settings.rag_embedding_dim
        key = api_key or settings.cohere_api_key
        if not key:
            logger.warning("COHERE_API_KEY is not set; CohereEmbedder will fail when called")
        # Lazy import — cohere is optional dependency
        import cohere
        verify = not settings.llm_insecure_tls
        if not verify:
            import httpx
            httpx_client = httpx.AsyncClient(verify=False)
            self._client = cohere.AsyncClientV2(api_key=key or "placeholder", httpx_client=httpx_client)
        else:
            self._client = cohere.AsyncClientV2(api_key=key or "placeholder")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors: List[List[float]] = []
        try:
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = texts[start : start + _BATCH_SIZE]
                resp = await self._client.embed(
                    model=self.model,
                    texts=batch,
                    input_type="search_document",
                    embedding_types=["float"],
                )
                vectors.extend(resp.embeddings.float_)
            return vectors
        except Exception as e:
            logger.error(f"Cohere embedding failed: {e}")
            raise RAGEmbedFailed(str(e)) from e
