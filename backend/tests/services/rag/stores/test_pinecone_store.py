"""Live integration tests for PineconeStore.

Skipped unless PINECONE_API_KEY and PINECONE_INDEX_NAME are set in the env.
Requires an actual Pinecone index with dim=1536 and cosine metric.
"""
import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

requires_pinecone = pytest.mark.skipif(
    not (os.getenv("PINECONE_API_KEY") and os.getenv("PINECONE_INDEX_NAME")),
    reason="PINECONE_API_KEY and PINECONE_INDEX_NAME must be set for live tests",
)


@pytest.mark.pinecone
@requires_pinecone
def test_store_constructs_and_reports_name():
    from app.services.rag.stores.pinecone_store import PineconeStore
    store = PineconeStore()
    assert store.name == "pinecone"


@pytest.mark.pinecone
@requires_pinecone
def test_health_returns_true_on_live_index():
    from app.services.rag.stores.pinecone_store import PineconeStore
    store = PineconeStore()
    ok = asyncio.run(store.health())
    assert ok is True


def test_constructor_raises_rag_degraded_without_api_key(monkeypatch):
    """Pure-unit: missing config → RAGDegraded, no SDK import attempted."""
    from app.services.rag.errors import RAGDegraded
    from app.config import settings as s

    monkeypatch.setattr(s, "pinecone_api_key", "")
    monkeypatch.setattr(s, "pinecone_index_name", "")

    from app.services.rag.stores.pinecone_store import PineconeStore
    with pytest.raises(RAGDegraded):
        PineconeStore()


@pytest.mark.pinecone
@requires_pinecone
def test_upsert_and_delete_rules_round_trip():
    """Upsert two rules, query top-1 vector, then delete and verify gone."""
    import uuid as _uuid
    from app.services.rag.ports import VectorDoc
    from app.services.rag.stores.pinecone_store import PineconeStore

    store = PineconeStore()
    dim = store._dim
    ids = [str(_uuid.uuid4()), str(_uuid.uuid4())]
    docs = [
        VectorDoc(
            id=ids[0],
            embedding=[0.01] * dim,
            fields={"category": "irdai", "severity": "high", "is_active": True, "rule_text": "T1"},
        ),
        VectorDoc(
            id=ids[1],
            embedding=[0.02] * dim,
            fields={"category": "sebi", "severity": "low", "is_active": True, "rule_text": "T2"},
        ),
    ]

    asyncio.run(store.upsert("rag_rules", docs))

    # Round-trip vector search to confirm both landed.
    hits = asyncio.run(store.vector_search(
        index="rag_rules", query_vector=[0.01] * dim, top_k=10,
    ))
    found_ids = {h.id for h in hits}
    assert ids[0] in found_ids
    assert ids[1] in found_ids

    # Cleanup.
    asyncio.run(store.delete("rag_rules", ids))


@pytest.mark.pinecone
@requires_pinecone
def test_upsert_empty_list_is_noop():
    from app.services.rag.stores.pinecone_store import PineconeStore
    store = PineconeStore()
    asyncio.run(store.upsert("rag_rules", []))  # must not raise


@pytest.mark.pinecone
@requires_pinecone
def test_vector_search_returns_hits_with_metadata():
    """Upsert, search, expect the document to come back with metadata fields."""
    import uuid as _uuid
    from app.services.rag.ports import VectorDoc
    from app.services.rag.stores.pinecone_store import PineconeStore

    store = PineconeStore()
    dim = store._dim
    rid = str(_uuid.uuid4())
    asyncio.run(store.upsert("rag_rules", [
        VectorDoc(id=rid, embedding=[0.5] * dim, fields={
            "category": "irdai", "severity": "high", "is_active": True, "rule_text": "FindMe",
        }),
    ]))
    try:
        hits = asyncio.run(store.vector_search(
            index="rag_rules", query_vector=[0.5] * dim, top_k=3,
        ))
        assert any(h.id == rid for h in hits)
        match = next(h for h in hits if h.id == rid)
        assert match.fields.get("rule_text") == "FindMe"
        assert match.fields.get("category") == "irdai"
        assert match.score > 0.0
    finally:
        asyncio.run(store.delete("rag_rules", [rid]))


@pytest.mark.pinecone
@requires_pinecone
def test_vector_search_applies_filter():
    """Two rules with different categories; filter narrows to one."""
    import uuid as _uuid
    from app.services.rag.ports import VectorDoc
    from app.services.rag.stores.pinecone_store import PineconeStore

    store = PineconeStore()
    dim = store._dim
    rid_a = str(_uuid.uuid4())
    rid_b = str(_uuid.uuid4())
    asyncio.run(store.upsert("rag_rules", [
        VectorDoc(id=rid_a, embedding=[0.7] * dim, fields={"category": "irdai", "is_active": True}),
        VectorDoc(id=rid_b, embedding=[0.7] * dim, fields={"category": "sebi", "is_active": True}),
    ]))
    try:
        hits = asyncio.run(store.vector_search(
            index="rag_rules", query_vector=[0.7] * dim, top_k=10,
            filters={"category": "irdai"},
        ))
        found = {h.id for h in hits}
        assert rid_a in found
        assert rid_b not in found
    finally:
        asyncio.run(store.delete("rag_rules", [rid_a, rid_b]))


@pytest.mark.pinecone
@requires_pinecone
def test_hybrid_search_aliases_vector_search():
    """hybrid_search() should accept query_text/recall_pool/rrf_k but ignore them."""
    import uuid as _uuid
    from app.services.rag.ports import VectorDoc
    from app.services.rag.stores.pinecone_store import PineconeStore

    store = PineconeStore()
    dim = store._dim
    rid = str(_uuid.uuid4())
    asyncio.run(store.upsert("rag_rules", [
        VectorDoc(id=rid, embedding=[0.3] * dim, fields={"category": "irdai"}),
    ]))
    try:
        hits = asyncio.run(store.hybrid_search(
            index="rag_rules",
            query_text="this text is ignored",
            query_vector=[0.3] * dim,
            top_k=3, recall_pool=30, rrf_k=60,
        ))
        assert any(h.id == rid for h in hits)
    finally:
        asyncio.run(store.delete("rag_rules", [rid]))
