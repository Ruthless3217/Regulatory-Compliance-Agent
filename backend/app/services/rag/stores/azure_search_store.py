"""Azure AI Search vector store (v2 backend, activated once subscription lands).

Speaks the same VectorStore protocol as PgVectorStore. Uses the
`azure-search-documents` SDK. The SDK is imported lazily so the package
isn't a hard dependency for v1 deployments.

Index names: rag-rules, rag-chunks, rag-source-docs (with hyphens, since
underscores are not allowed in Azure Search index names). The mapping
from logical names ('rag_rules' etc.) to Azure index names is local here.

Hybrid search + semantic re-ranking are issued as a single request, so we
don't import the RRF helper here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.ports import IndexName, SearchHit, VectorDoc

logger = logging.getLogger(__name__)


_INDEX_MAP: Dict[IndexName, str] = {
    "rag_rules": "azure_search_rules_index",
    "rag_chunks": "azure_search_chunks_index",
    "rag_source_docs": "azure_search_source_docs_index",
}


def _azure_filter(filters: Optional[Dict[str, Any]]) -> Optional[str]:
    """Translate the generic filter dict to an OData filter string."""
    if not filters:
        return None
    parts: List[str] = []
    for k, v in filters.items():
        if isinstance(v, (list, tuple, set)):
            if not v:
                parts.append("false")
                continue
            inner = ",".join(f"'{x}'" if isinstance(x, str) else str(x) for x in v)
            parts.append(f"search.in({k}, '{inner}', ',')")
        elif isinstance(v, bool):
            parts.append(f"{k} eq {'true' if v else 'false'}")
        elif isinstance(v, str):
            parts.append(f"{k} eq '{v}'")
        else:
            parts.append(f"{k} eq {v}")
    return " and ".join(parts) if parts else None


class AzureSearchStore:
    """VectorStore backed by Azure AI Search with hybrid + semantic re-rank."""

    name = "azure_search"

    def __init__(self) -> None:
        try:
            from azure.search.documents.aio import SearchClient  # noqa: F401
            from azure.core.credentials import AzureKeyCredential  # noqa: F401
        except ImportError as e:
            raise RAGDegraded(
                "azure-search-documents not installed; pip install azure-search-documents>=11.5.0"
            ) from e

        if not settings.azure_search_endpoint or not settings.azure_search_api_key:
            raise RAGDegraded("AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_API_KEY not configured")

        self._endpoint = settings.azure_search_endpoint
        self._key = settings.azure_search_api_key
        self._clients: Dict[str, Any] = {}  # cached per-index SearchClient

    def _client(self, index: IndexName):
        from azure.search.documents.aio import SearchClient
        from azure.core.credentials import AzureKeyCredential

        setting_name = _INDEX_MAP.get(index)
        if setting_name is None:
            raise RAGDegraded(f"no Azure Search index mapping for {index!r}")
        index_name = getattr(settings, setting_name)
        if index_name not in self._clients:
            self._clients[index_name] = SearchClient(
                endpoint=self._endpoint,
                index_name=index_name,
                credential=AzureKeyCredential(self._key),
            )
        return self._clients[index_name]

    @staticmethod
    def _doc_payload(index: IndexName, doc: VectorDoc) -> Dict[str, Any]:
        return {"id": doc.id, "embedding": doc.embedding, **doc.fields}

    async def upsert(self, index: IndexName, docs: List[VectorDoc]) -> None:
        if not docs:
            return
        try:
            client = self._client(index)
            payload = [self._doc_payload(index, d) for d in docs]
            await client.merge_or_upload_documents(documents=payload)
        except Exception as e:
            raise RAGIndexingFailed(f"Azure Search upsert into {index} failed: {e}") from e

    async def delete(self, index: IndexName, ids: List[str]) -> None:
        if not ids:
            return
        try:
            client = self._client(index)
            await client.delete_documents(documents=[{"id": i} for i in ids])
        except Exception as e:
            raise RAGIndexingFailed(f"Azure Search delete from {index} failed: {e}") from e

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
        from azure.search.documents.models import VectorizedQuery

        try:
            client = self._client(index)
            vq = VectorizedQuery(vector=query_vector, k_nearest_neighbors=recall_pool, fields="embedding")

            results = await client.search(
                search_text=query_text or "*",
                vector_queries=[vq],
                filter=_azure_filter(filters),
                query_type="semantic",
                semantic_configuration_name=f"{index.replace('_', '-')}-semantic",
                top=top_k,
            )

            hits: List[SearchHit] = []
            async for r in results:
                doc_id = r.get("id")
                # Prefer reranker score when present, fall back to @search.score
                score = float(r.get("@search.reranker_score") or r.get("@search.score") or 0.0)
                fields = {k: v for k, v in r.items() if not k.startswith("@") and k != "embedding"}
                hits.append(SearchHit(id=str(doc_id), score=score, fields=fields))
            return hits
        except Exception as e:
            logger.error(f"Azure Search hybrid_search on {index} failed: {e}")
            raise RAGDegraded(str(e)) from e

    async def vector_search(
        self,
        index: IndexName,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        from azure.search.documents.models import VectorizedQuery

        try:
            client = self._client(index)
            vq = VectorizedQuery(vector=query_vector, k_nearest_neighbors=top_k, fields="embedding")
            results = await client.search(
                search_text=None,
                vector_queries=[vq],
                filter=_azure_filter(filters),
                top=top_k,
            )
            hits: List[SearchHit] = []
            async for r in results:
                doc_id = r.get("id")
                score = float(r.get("@search.score") or 0.0)
                fields = {k: v for k, v in r.items() if not k.startswith("@") and k != "embedding"}
                hits.append(SearchHit(id=str(doc_id), score=score, fields=fields))
            return hits
        except Exception as e:
            logger.error(f"Azure Search vector_search on {index} failed: {e}")
            raise RAGDegraded(str(e)) from e

    async def health(self) -> bool:
        try:
            client = self._client("rag_rules")
            await client.get_document_count()
            return True
        except Exception:
            return False
