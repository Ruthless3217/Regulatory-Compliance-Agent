"""Unit tests for compliance_examples_indexer.upsert_examples — embed-count guard."""
import asyncio
import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    ),
)

import app.services.rag.indexers.compliance_examples_indexer as indexer_mod
from app.services.rag.errors import RAGIndexingFailed


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

class _FakeEmbedder:
    """Returns a fixed list of vectors, regardless of input length."""

    def __init__(self, vectors):
        self._vectors = vectors

    async def embed(self, texts):
        return self._vectors


class _FakeStore:
    async def upsert(self, index_name, docs):
        pass

    async def search(self, *args, **kwargs):
        return []


# ---------------------------------------------------------------------------
# Helpers to patch the module-level factories
# ---------------------------------------------------------------------------

def _patch(embedder, store):
    """Monkeypatch get_embedder / get_vector_store in the indexer module."""
    original_embedder = indexer_mod.get_embedder
    original_store = indexer_mod.get_vector_store
    indexer_mod.get_embedder = lambda: embedder
    indexer_mod.get_vector_store = lambda: store
    return original_embedder, original_store


def _restore(original_embedder, original_store):
    indexer_mod.get_embedder = original_embedder
    indexer_mod.get_vector_store = original_store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_upsert_examples_raises_when_embed_count_short():
    """embedder returning fewer vectors than rows must raise RAGIndexingFailed."""
    rows = [
        {"id": "a", "chunk_text": "text A", "comment_text": "comment A"},
        {"id": "b", "chunk_text": "text B", "comment_text": "comment B"},
        {"id": "c", "chunk_text": "text C", "comment_text": "comment C"},
    ]
    # embedder returns only 2 vectors for 3 rows — partial-batch failure
    fake_embedder = _FakeEmbedder(vectors=[[0.1, 0.2], [0.3, 0.4]])
    fake_store = _FakeStore()

    orig_e, orig_s = _patch(fake_embedder, fake_store)
    try:
        raised = False
        try:
            asyncio.run(indexer_mod.upsert_examples(rows))
        except RAGIndexingFailed as exc:
            raised = True
            assert "2 vectors" in str(exc) and "3 rows" in str(exc), (
                f"Error message should mention counts; got: {exc}"
            )
        assert raised, "RAGIndexingFailed was not raised for short embed count"
    finally:
        _restore(orig_e, orig_s)


def test_upsert_examples_raises_when_embed_count_long():
    """embedder returning more vectors than rows must also raise RAGIndexingFailed."""
    rows = [
        {"id": "x", "chunk_text": "text X", "comment_text": "comment X"},
    ]
    # embedder returns 3 vectors for 1 row
    fake_embedder = _FakeEmbedder(vectors=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]])
    fake_store = _FakeStore()

    orig_e, orig_s = _patch(fake_embedder, fake_store)
    try:
        raised = False
        try:
            asyncio.run(indexer_mod.upsert_examples(rows))
        except RAGIndexingFailed:
            raised = True
        assert raised, "RAGIndexingFailed was not raised for long embed count"
    finally:
        _restore(orig_e, orig_s)


def test_upsert_examples_succeeds_when_counts_match():
    """Happy path: correct vector count should not raise and returns count."""
    rows = [
        {"id": "p", "chunk_text": "text P", "comment_text": "comment P"},
        {"id": "q", "chunk_text": "text Q", "comment_text": "comment Q"},
    ]
    fake_embedder = _FakeEmbedder(vectors=[[0.1, 0.2], [0.3, 0.4]])
    fake_store = _FakeStore()

    orig_e, orig_s = _patch(fake_embedder, fake_store)
    try:
        result = asyncio.run(indexer_mod.upsert_examples(rows))
        assert result == 2
    finally:
        _restore(orig_e, orig_s)
