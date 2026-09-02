"""AzureSearchStore._client must degrade gracefully, not KeyError, when asked
for an index that has no Azure Search settings entry yet (rag_compliance_examples,
rag_product_docs, precedent_cases — see _INDEX_MAP)."""
import pytest

from app.config import settings
from app.services.rag.errors import RAGDegraded
from app.services.rag.stores.azure_search_store import AzureSearchStore


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(settings, "azure_search_endpoint", "https://example.search.windows.net")
    monkeypatch.setattr(settings, "azure_search_api_key", "test-key")
    return AzureSearchStore()


def test_unmapped_index_raises_rag_degraded_not_keyerror(store):
    with pytest.raises(RAGDegraded):
        store._client("precedent_cases")


def test_mapped_index_still_resolves_a_client(store):
    client = store._client("rag_rules")
    assert client is not None
