"""OpenAI text-embedding-3-small embedder (v1 default).

Uses the OpenAI async client directly. Batches inputs to avoid round-trip
overhead. Raises RAGEmbedFailed on any non-success outcome so callers can
fall back gracefully.
"""
from __future__ import annotations

import logging
from typing import List

from openai import AsyncOpenAI

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

logger = logging.getLogger(__name__)

# OpenAI accepts up to 2048 inputs per call; we cap lower for latency.
_BATCH_SIZE = 96


class OpenAIEmbedder:
    """Embedder backed by OpenAI's hosted embedding API."""

    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.model = model or settings.rag_embedding_model
        self.dim = settings.rag_embedding_dim
        key = api_key or settings.openai_api_key
        if not key:
            logger.warning(
                "OPENAI_API_KEY is not set; OpenAIEmbedder will fail when called"
            )
        # OpenAI defaults to https://api.openai.com/v1
        self._client = AsyncOpenAI(api_key=key or "placeholder")

    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        # input_type is accepted for protocol parity with asymmetric models
        # (Cohere); OpenAI embeddings are symmetric so it is ignored here.
        if not texts:
            return []
        vectors: List[List[float]] = []
        try:
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = texts[start : start + _BATCH_SIZE]
                resp = await self._client.embeddings.create(
                    model=self.model, input=batch
                )
                vectors.extend(item.embedding for item in resp.data)
            return vectors
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
            raise RAGEmbedFailed(str(e)) from e
