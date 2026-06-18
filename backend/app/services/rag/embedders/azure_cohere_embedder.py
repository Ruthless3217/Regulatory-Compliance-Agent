"""Azure AI Foundry Cohere embedder (embed v3, 1024-dim).

Cohere models deployed in an Azure AI Foundry project are served from the
model-inference endpoint (``https://<resource>.services.ai.azure.com/models``),
NOT the Azure OpenAI surface (``.openai.azure.com``). We reach them with the
``azure-ai-inference`` SDK's ``EmbeddingsClient``, which speaks the catalog
model contract for Cohere.

Cohere v3 is asymmetric, so we map the protocol's ``input_type``
('search_document' / 'search_query') to the SDK's ``EmbeddingInputType``
(DOCUMENT / QUERY). Output is 1024-dim — identical to Cohere's public
``embed-english-v3.0`` — so an index built against the public Cohere API stays
compatible after the swap (no re-index for the english variant).
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import List

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

logger = logging.getLogger(__name__)

_BATCH_SIZE = 96
# Cohere embed v3 family is 1024-dim (english and multilingual alike).
_COHERE_V3_DIM = 1024


class AzureCohereEmbedder:
    """Embedder backed by a Cohere embed deployment in Azure AI Foundry."""

    name = "azure_cohere"

    def __init__(self) -> None:
        self.model = settings.azure_cohere_embed_deployment
        # Dim is a property of the model, not the shared rag_embedding_dim
        # (which may still default to OpenAI's 1536). Pin to the Cohere v3 dim.
        self.dim = _COHERE_V3_DIM

        endpoint = settings.azure_inference_endpoint
        # Embed and the chat LLM live in the same Foundry resource, so the
        # LLM key works here when a dedicated inference key isn't set.
        key = settings.azure_inference_api_key or (
            settings.llm_api_keys[0] if settings.llm_api_keys else ""
        )
        if not endpoint or not key:
            logger.warning(
                "AZURE_INFERENCE_ENDPOINT/API_KEY not set; "
                "AzureCohereEmbedder will fail when called"
            )

        # Lazy import — azure-ai-inference is only needed for this provider.
        from azure.ai.inference.aio import EmbeddingsClient
        from azure.core.credentials import AzureKeyCredential

        client_kwargs = dict(
            endpoint=endpoint or "https://placeholder.services.ai.azure.com/models",
            credential=AzureKeyCredential(key or "placeholder"),
            api_version=settings.azure_inference_api_version,
        )
        if settings.llm_insecure_tls:
            # Behind corporate SSL inspection (Cisco) the verified handshake
            # fails; bypass verify so the external HTTPS call works. Mirrors the
            # LLM client's LLM_INSECURE_TLS path.
            from azure.core.pipeline.transport import AioHttpTransport
            client_kwargs["transport"] = AioHttpTransport(connection_verify=False)

        self._client = EmbeddingsClient(**client_kwargs)
        self._init_cache()

    def _init_cache(self) -> None:
        """Bounded LRU of text → vector. Repeated chunks across submissions are
        common; caching avoids paying Azure to re-embed them."""
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._cache_size = max(0, settings.embed_cache_size)

    def _key(self, text: str, input_type: str) -> str:
        # Same text embedded as query vs document yields different vectors, and
        # vectors from a different model are incomparable — key on all three.
        return f"{self.model}\x00{input_type}\x00{text}"

    def _cache_get(self, key: str):
        v = self._cache.get(key)
        if v is not None:
            self._cache.move_to_end(key)
        return v

    def _cache_put(self, key: str, vector: List[float]) -> None:
        if self._cache_size <= 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def _embed_batch(self, batch: List[str], input_type: str) -> List[List[float]]:
        from azure.ai.inference.models import EmbeddingInputType

        sdk_input_type = (
            EmbeddingInputType.QUERY
            if input_type == "search_query"
            else EmbeddingInputType.DOCUMENT
        )
        resp = await self._client.embed(
            input=batch,
            model=self.model,
            input_type=sdk_input_type,
        )
        # Azure returns items ordered by `index`; sort defensively before use.
        items = sorted(resp.data, key=lambda d: d.index)
        return [list(item.embedding) for item in items]

    async def embed(
        self, texts: List[str], input_type: str = "search_document"
    ) -> List[List[float]]:
        """Embed texts, serving cache hits and embedding only the misses.

        ``input_type`` is 'search_document' for indexing and 'search_query' for
        retrieval queries (Cohere v3 is asymmetric).
        """
        if not texts:
            return []

        results: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]
        misses: List[str] = []
        miss_pos: dict = {}

        for i, t in enumerate(texts):
            cached = self._cache_get(self._key(t, input_type))
            if cached is not None:
                results[i] = cached
            elif t not in miss_pos:
                miss_pos[t] = len(misses)
                misses.append(t)

        try:
            miss_vecs: List[List[float]] = []
            for start in range(0, len(misses), _BATCH_SIZE):
                batch = misses[start : start + _BATCH_SIZE]
                miss_vecs.extend(await self._embed_batch(batch, input_type))
        except Exception as e:
            logger.error(f"Azure Cohere embedding failed: {e}")
            raise RAGEmbedFailed(str(e)) from e

        for t, vec in zip(misses, miss_vecs):
            self._cache_put(self._key(t, input_type), vec)

        for i, t in enumerate(texts):
            if results[i] is None:
                results[i] = miss_vecs[miss_pos[t]]
        return results
