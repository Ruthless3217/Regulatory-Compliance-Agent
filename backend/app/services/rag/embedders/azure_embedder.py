"""Azure OpenAI embedder (v2, swap-in when Azure subscription available).

Contract identical to OpenAIEmbedder. Uses AsyncAzureOpenAI from the same
openai SDK — endpoint, deployment name, and api version come from settings.
"""
from __future__ import annotations

import logging
from typing import List

from openai import AsyncAzureOpenAI

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

logger = logging.getLogger(__name__)

_BATCH_SIZE = 96


class AzureOpenAIEmbedder:
    """Embedder backed by an Azure OpenAI deployment."""

    name = "azure_openai"

    def __init__(self) -> None:
        self.model = settings.azure_openai_embed_deployment
        self.dim = settings.rag_embedding_dim

        if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
            logger.warning(
                "Azure OpenAI endpoint/key not set; AzureOpenAIEmbedder will fail when called"
            )

        self._client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key or "placeholder",
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint or "https://placeholder.openai.azure.com",
        )

    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        # input_type ignored (Azure OpenAI embeddings are symmetric); accepted
        # for protocol parity with the asymmetric Cohere embedder.
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
            logger.error(f"Azure OpenAI embedding failed: {e}")
            raise RAGEmbedFailed(str(e)) from e
