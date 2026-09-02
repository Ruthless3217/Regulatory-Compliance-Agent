"""GET /health/rag and POST /debug/rag/search used to have no auth dependency
at all — anyone could run arbitrary hybrid_search queries against the internal
rule/chunk/source-doc corpus with no session and no scope check. Both routes
now require knowledgebase:view, matching the sibling search route in
knowledge_base.py.
"""
import asyncio

from fastapi.testclient import TestClient

from app.api.routes import rag_health
from app.main import app
from app.services.rag.errors import RAGDegraded

client = TestClient(app)


def test_health_rag_without_a_session_is_a_401():
    resp = client.get("/health/rag")
    assert resp.status_code == 401


def test_debug_rag_search_without_a_session_is_a_401():
    resp = client.post("/debug/rag/search", json={"query": "capital requirements"})
    assert resp.status_code == 401


def test_health_rag_reports_a_disabled_provider_instead_of_500ing(monkeypatch):
    # Seen live: RAG_EMBEDDING_PROVIDER=cohere (removed) made get_embedder()
    # raise before the probe had built any payload, so the health route 500ed.
    def _boom():
        raise RAGDegraded("RAG_EMBEDDING_PROVIDER 'cohere' is disabled/removed.")
    monkeypatch.setattr(rag_health, "get_embedder", _boom)
    body = asyncio.run(rag_health.health_rag(db=None, user={"role": "admin"}))
    assert body["embedder_ok"] is False
    assert body["vector_store_ok"] is False
    assert "cohere" in body["embedder_error"]
