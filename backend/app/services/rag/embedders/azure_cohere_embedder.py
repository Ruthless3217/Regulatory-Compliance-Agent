"""Cohere embeddings served through the Azure AI Foundry `/models` inference
endpoint (RAG_EMBEDDING_PROVIDER=azure_cohere).

Unlike the public Cohere API (cohere_embedder) or the Azure OpenAI embedding
surface (azure_embedder), Cohere embed models on Azure AI Foundry are reached
via the standardized Azure AI Model Inference API:

 POST {AZURE_INFERENCE_ENDPOINT}/embeddings?api-version=...

We use the official `azure-ai-inference` async client, which builds that URL,
attaches the Foundry api-key, and serializes Cohere's required `input_type`
(asymmetric v3 models embed documents and queries differently — audit H18).

The deployment name doubles as the `model` identity: pgvector stamps it on every
row, and the query-time fail-closed guard rejects retrieval if the active
embedder's model differs from what indexed the rows. Ingest and query both go
through the singleton embedder, so the stamp is consistent.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.config import settings
from app.services.rag.errors import RAGEmbedFailed

try:
 from langsmith import traceable
except Exception: # pragma: no cover
 def traceable(*_a, **_kw): # type: ignore
 def _d(fn): return fn
 return _d if not (_a and callable(_a[0])) else _a[0]

logger = logging.getLogger(__name__)

# Cohere caps a single embed request at 96 inputs.
_BATCH_SIZE = 96

# Foundry deployment → output dimensionality. The dim is a property of the
# underlying Cohere model, not of the shared rag_embedding_dim setting. Deriving
# it here prevents a silent dim mismatch against the pgvector column (audit H2).
_DEPLOYMENT_DIMS = {
 "Cohere-embed-v3-multilingual": 1024,
 "Cohere-embed-v3-english": 1024,
}

# Embedder protocol input_type → Azure AI Model Inference input_type. The Azure
# schema uses "document"/"query"/"text"; our callers speak Cohere's vocabulary.
_INPUT_TYPE_MAP = {
 "search_document": "document",
 "search_query": "query",
}


class AzureCohereEmbedder:
 """Embedder backed by a Cohere deployment on Azure AI Foundry."""

 name = "azure_cohere"

 def __init__(
 self,
 api_key: Optional[str] = None,
 deployment: Optional[str] = None,
 client=None,
 ) -> None:
 self.model = deployment or settings.azure_cohere_embed_deployment
 self.dim = _DEPLOYMENT_DIMS.get(self.model, settings.rag_embedding_dim)

 # Dependency injection for tests — skip SDK/network entirely.
 if client is not None:
 self._client = client
 return

 key = api_key or settings.azure_inference_api_key or settings.llm_api_key
 endpoint = settings.azure_inference_endpoint
 if not endpoint or not key:
 logger.warning(
 "AZURE_INFERENCE_ENDPOINT / key not set; "
 "AzureCohereEmbedder will fail when called"
 )

 # Lazy import — azure-ai-inference is only needed for this provider.
 from azure.ai.inference.aio import EmbeddingsClient
 from azure.core.credentials import AzureKeyCredential

 # Bypass TLS verify behind corporate (Cisco) SSL inspection, matching the
 # LLM/Cohere paths. connection_verify is forwarded to the async transport.
 verify = not settings.llm_insecure_tls
 self._client = EmbeddingsClient(
 endpoint=endpoint or "https://placeholder.services.ai.azure.com/models",
 credential=AzureKeyCredential(key or "placeholder"),
 api_version=settings.azure_inference_api_version,
 connection_verify=verify,
 )

 async def _embed_batch(self, batch: List[str], input_type: str) -> List[List[float]]:
 resp = await self._client.embed(
 input=batch,
 model=self.model,
 input_type=input_type,
 )
 # The service may return items out of submission order; re-sort by index
 # so vectors line up with the input texts.
 ordered = sorted(resp.data, key=lambda item: item.index)
 return [list(item.embedding) for item in ordered]

 @traceable(run_type="embedding", name="AzureCohere.embed")
 async def embed(
 self, texts: List[str], input_type: str = "search_document"
 ) -> List[List[float]]:
 """Embed texts, preserving order. ``input_type`` is 'search_document' for
 indexed content and 'search_query' for retrieval queries (Cohere v3 is
 asymmetric — audit H18)."""
 if not texts:
 return []

 azure_input_type = _INPUT_TYPE_MAP.get(input_type, "document")
 vectors: List[List[float]] = []
 try:
 for start in range(0, len(texts), _BATCH_SIZE):
 batch = texts[start : start + _BATCH_SIZE]
 vectors.extend(await self._embed_batch(batch, azure_input_type))
 return vectors
 except Exception as e:
 logger.error(f"Azure Cohere embedding failed: {e}")
 raise RAGEmbedFailed(str(e)) from e
