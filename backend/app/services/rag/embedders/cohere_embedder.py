"""Cohere embed-english-v3.0 embedder (1024-dim, alternative to OpenAI).

Cohere requires an `input_type` per call. We use 'search_document' for
indexing and 'search_query' for retrieval queries. To keep the Embedder
protocol simple, both retrievers and indexers route through `embed()`;
internally we default to 'search_document' which gives the right vectors
for upsert. Query-time vectors are slightly off-distribution but Cohere's
asymmetric models still match well enough for v1.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import List

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _d(fn): return fn
        return _d if not (_a and callable(_a[0])) else _a[0]

logger = logging.getLogger(__name__)

_BATCH_SIZE = 96
_RATE_LIMIT_MAX_RETRIES = 6
_RATE_LIMIT_BACKOFF_SECONDS = (30, 60, 90, 120, 150, 180)

# Cohere embedding model → output dimensionality. The dim is a property of the
# model, NOT of the shared rag_embedding_dim setting (which defaults to OpenAI's
# 1536). Deriving it here prevents the silent 1024-vs-1536 mismatch (audit H2).
_MODEL_DIMS = {
    "embed-english-v3.0": 1024,
    "embed-multilingual-v3.0": 1024,
    "embed-english-light-v3.0": 384,
    "embed-multilingual-light-v3.0": 384,
}


def _is_rate_limit(err: Exception) -> bool:
    name = type(err).__name__.lower()
    if "toomanyrequests" in name or "ratelimit" in name:
        return True
    msg = str(err).lower()
    return "status_code: 429" in msg or "rate limit" in msg


class CohereEmbedder:
    """Embedder backed by Cohere's hosted embedding API."""

    name = "cohere"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.model = model or settings.cohere_embedding_model
        # Derive dim from the model (audit H2). Fall back to the configured
        # dim only for unknown models.
        self.dim = _MODEL_DIMS.get(self.model, settings.rag_embedding_dim)
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
        self._init_cache()

    def _init_cache(self) -> None:
        """Bounded LRU of text → vector (Priority 4d). Repeated chunks across
        submissions are common; caching avoids paying Cohere for them twice."""
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._cache_size = max(0, settings.embed_cache_size)

    def _key(self, text: str, input_type: str) -> str:
        """Cache key. Must include model + input_type: the same text embedded
        as a query vs a document yields different vectors, and vectors from a
        different model are incomparable (audit H19)."""
        return f"{self.model}\x00{input_type}\x00{text}"

    def _cache_get(self, key: str):
        v = self._cache.get(key)
        if v is not None:
            self._cache.move_to_end(key)  # LRU touch
        return v

    def _cache_put(self, key: str, vector: List[float]) -> None:
        if self._cache_size <= 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)  # evict least-recently-used

    async def _embed_batch(self, batch: List[str], input_type: str) -> List[List[float]]:
        attempt = 0
        while True:
            try:
                resp = await self._client.embed(
                    model=self.model,
                    texts=batch,
                    input_type=input_type,
                    embedding_types=["float"],
                )
                return list(resp.embeddings.float_)
            except Exception as e:
                if _is_rate_limit(e) and attempt < _RATE_LIMIT_MAX_RETRIES:
                    delay = _RATE_LIMIT_BACKOFF_SECONDS[
                        min(attempt, len(_RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        f"Cohere 429 (rate limit) on batch of {len(batch)}; "
                        f"sleeping {delay}s before retry {attempt + 1}/{_RATE_LIMIT_MAX_RETRIES}"
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise

    @traceable(run_type="embedding", name="Cohere.embed")
    async def embed(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        """Embed texts, serving cache hits and embedding only the misses.

        ``input_type`` is 'search_document' for indexing and 'search_query' for
        retrieval queries (Cohere v3 is asymmetric — audit H18). Repeated chunks
        across submissions are common, so cached vectors avoid paying Cohere
        twice (audit H19).
        """
        if not texts:
            return []

        results: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]
        misses: List[str] = []          # unique miss texts, first-seen order
        miss_pos: dict = {}             # text -> index in `misses`

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
            logger.error(f"Cohere embedding failed: {e}")
            raise RAGEmbedFailed(str(e)) from e

        for t, vec in zip(misses, miss_vecs):
            self._cache_put(self._key(t, input_type), vec)

        for i, t in enumerate(texts):
            if results[i] is None:
                results[i] = miss_vecs[miss_pos[t]]
        return results
