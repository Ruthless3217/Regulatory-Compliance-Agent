"""PrecedentRetriever.retrieve_per_chunk must fire its per-chunk hybrid_search
calls concurrently (like RulesRetriever does for chunk x category), not one
at a time — and a RAGDegraded on one chunk must not affect the others or
their result ordering.
"""
import asyncio
from typing import Any, Dict

import pytest

from app.services.rag.errors import RAGDegraded
from app.services.rag.ports import SearchHit
from app.services.rag.retrievers import precedent_retriever as mod


class _FakeEmbedder:
    async def embed(self, texts, input_type=None):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _BarrierStore:
    """Every hybrid_search call blocks until ALL expected calls have arrived.

    If retrieve_per_chunk awaited each chunk's query sequentially (the old
    for-loop), the first call would block forever waiting for calls that
    can never arrive while it holds the loop — this test would time out
    instead of passing, which is exactly the regression being pinned.
    """

    def __init__(self, n_expected: int, fail_query_text: str = None):
        self.calls: list = []
        self._n_expected = n_expected
        self._entered = 0
        self._event = asyncio.Event()
        self._fail_query_text = fail_query_text

    async def hybrid_search(self, **kwargs: Any) -> list:
        self.calls.append(kwargs)
        self._entered += 1
        if self._entered >= self._n_expected:
            self._event.set()
        await asyncio.wait_for(self._event.wait(), timeout=2)

        query_text = kwargs["query_text"]
        if query_text == self._fail_query_text:
            raise RAGDegraded("simulated store failure")
        return [
            SearchHit(
                id=f"hit-{query_text}",
                score=0.9,
                fields={
                    "reviewer_role": "reviewer",
                    "reviewer_comment": f"comment for {query_text}",
                },
            )
        ]


def _patch(monkeypatch, store):
    monkeypatch.setattr(mod, "get_vector_store", lambda: store)
    monkeypatch.setattr(mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(mod, "_check_precedent_cases_populated", lambda: True)


def test_all_chunks_are_queried_concurrently(monkeypatch):
    chunks = [{"id": "c1", "text": "c1"}, {"id": "c2", "text": "c2"}, {"id": "c3", "text": "c3"}]
    store = _BarrierStore(n_expected=len(chunks))
    _patch(monkeypatch, store)

    out = asyncio.run(mod.PrecedentRetriever().retrieve_per_chunk(chunks=chunks))

    assert len(store.calls) == 3
    assert len(out["c1"]) == 1 and len(out["c2"]) == 1 and len(out["c3"]) == 1


def test_rag_degraded_on_one_chunk_yields_empty_only_for_that_chunk(monkeypatch):
    chunks = [{"id": "c1", "text": "c1"}, {"id": "c2", "text": "c2"}, {"id": "c3", "text": "c3"}]
    store = _BarrierStore(n_expected=len(chunks), fail_query_text="c2")
    _patch(monkeypatch, store)

    out = asyncio.run(mod.PrecedentRetriever().retrieve_per_chunk(chunks=chunks))

    assert out["c2"] == []
    assert len(out["c1"]) == 1
    assert len(out["c3"]) == 1
    # Ordering stays deterministic (input chunk order), independent of which
    # concurrent task happens to finish first.
    assert list(out.keys()) == ["c1", "c2", "c3"]
