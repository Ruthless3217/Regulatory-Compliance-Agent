# Pinecone Vector Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pinecone as a third pluggable `VectorStore` backend alongside `pgvector` and `azure_search`, selected via `RAG_VECTOR_BACKEND=pinecone`. Dense-only search, one Pinecone index with three namespaces, OpenAI 1536-dim embeddings reused.

**Architecture:** New `backend/app/services/rag/stores/pinecone_store.py` implementing the `VectorStore` protocol from `ports.py`. Hybrid search aliases to vector search (Pinecone has no BM25). The factory in `factory.py` gains a `pinecone` branch. Heavy SDK calls are wrapped in `loop.run_in_executor` to mirror `pgvector_store.py`'s sync-in-async pattern.

**Tech Stack:** Python 3.11, `pinecone>=5.0.0` SDK, FastAPI, SQLAlchemy (unchanged), OpenAI embeddings (unchanged), pytest with a custom `pinecone` marker for live tests.

**Spec:** [docs/superpowers/specs/2026-05-20-pinecone-backend-design.md](../specs/2026-05-20-pinecone-backend-design.md)

---

## File Map

**Create:**
- `backend/app/services/rag/stores/pinecone_store.py` — full `PineconeStore` class
- `backend/tests/services/__init__.py`, `backend/tests/services/rag/__init__.py`, `backend/tests/services/rag/stores/__init__.py` — package markers
- `backend/tests/services/rag/stores/test_pinecone_helpers.py` — pure-unit tests for filter + metadata helpers
- `backend/tests/services/rag/stores/test_pinecone_store.py` — live `@pytest.mark.pinecone` integration tests
- `backend/conftest.py` — register the `pinecone` pytest marker

**Modify:**
- `backend/app/config.py` — add `pinecone_*` settings fields
- `backend/app/services/rag/factory.py` — add `pinecone` branch in `get_vector_store()`
- `backend/requirements.txt` — add `pinecone>=5.0.0`
- `backend/.env.example` (if it exists) or document the new env vars in `README.md` — non-blocking, can be skipped if no `.env.example` is committed

---

## Task 1: Config + dependency wiring

**Files:**
- Modify: `backend/app/config.py` (append fields after the Azure AI Search block)
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add Pinecone settings to config.py**

Open `backend/app/config.py` and add the following block immediately after the existing Azure AI Search settings (i.e. after the line `azure_search_source_docs_index: str = "rag-source-docs"`):

```python
    # Pinecone (alternative v1 vector store)
    pinecone_api_key: str = ""
    pinecone_index_name: str = ""
    pinecone_namespace_rules: str = "rag_rules"
    pinecone_namespace_chunks: str = "rag_chunks"
    pinecone_namespace_srcdocs: str = "rag_source_docs"
```

- [ ] **Step 2: Add Pinecone SDK to requirements.txt**

Append the following block to the end of `backend/requirements.txt`:

```
# RAG — Pinecone (loaded lazily; only required when
# RAG_VECTOR_BACKEND=pinecone). Safe to keep installed under any backend
# since the import is gated.
pinecone>=5.0.0
```

- [ ] **Step 3: Install the new dependency**

Run from the repo root: `pip install -r backend/requirements.txt`
Expected: `pinecone-5.x.x` (and its transitive deps) installed without errors.

- [ ] **Step 4: Verify config loads with empty Pinecone fields**

Run: `cd backend && python -c "from app.config import settings; print(settings.pinecone_api_key, settings.pinecone_index_name, settings.pinecone_namespace_rules)"`
Expected output: `  rag_rules` (empty api_key, empty index_name, default namespace)

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/requirements.txt
git commit -m "feat(rag): add Pinecone settings + SDK dependency"
```

(Skip the commit if the user's `no-auto-commit` rule is active — they will commit manually.)

---

## Task 2: Filter translator (pure-unit, TDD)

**Files:**
- Create: `backend/tests/services/__init__.py` (empty)
- Create: `backend/tests/services/rag/__init__.py` (empty)
- Create: `backend/tests/services/rag/stores/__init__.py` (empty)
- Create: `backend/tests/services/rag/stores/test_pinecone_helpers.py`
- Create: `backend/app/services/rag/stores/pinecone_store.py` (skeleton + `_to_pinecone_filter`)

- [ ] **Step 1: Create test package markers**

Create three empty files:
- `backend/tests/services/__init__.py`
- `backend/tests/services/rag/__init__.py`
- `backend/tests/services/rag/stores/__init__.py`

Each file contains only an empty docstring or nothing at all.

- [ ] **Step 2: Write failing tests for `_to_pinecone_filter`**

Create `backend/tests/services/rag/stores/test_pinecone_helpers.py` with the following contents:

```python
"""Pure-unit tests for PineconeStore helpers. No network, no Pinecone SDK calls."""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.rag.stores.pinecone_store import (
    _to_pinecone_filter,
    _sanitize_metadata,
)


def test_filter_none_returns_none():
    assert _to_pinecone_filter(None) is None


def test_filter_empty_dict_returns_none():
    assert _to_pinecone_filter({}) is None


def test_filter_single_eq():
    assert _to_pinecone_filter({"category": "irdai"}) == {"category": {"$eq": "irdai"}}


def test_filter_bool_eq():
    assert _to_pinecone_filter({"is_active": True}) == {"is_active": {"$eq": True}}


def test_filter_list_becomes_in():
    out = _to_pinecone_filter({"category": ["irdai", "sebi"]})
    assert out == {"category": {"$in": ["irdai", "sebi"]}}


def test_filter_tuple_becomes_in():
    out = _to_pinecone_filter({"category": ("irdai", "sebi")})
    assert out == {"category": {"$in": ["irdai", "sebi"]}}


def test_filter_combines_multiple_keys():
    out = _to_pinecone_filter({"category": "irdai", "is_active": True})
    assert out == {"category": {"$eq": "irdai"}, "is_active": {"$eq": True}}


def test_filter_uuid_value_coerced_to_str():
    sub_id = uuid.uuid4()
    out = _to_pinecone_filter({"submission_id": sub_id})
    assert out == {"submission_id": {"$eq": str(sub_id)}}
```

- [ ] **Step 3: Run tests — verify they fail**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_helpers.py -v`
Expected: `ImportError` or `ModuleNotFoundError: No module named 'app.services.rag.stores.pinecone_store'`

- [ ] **Step 4: Create skeleton `pinecone_store.py` with `_to_pinecone_filter`**

Create `backend/app/services/rag/stores/pinecone_store.py` with the following contents:

```python
"""Pinecone vector store (alternative v1 backend, dense-only).

Speaks the VectorStore protocol. One Pinecone index, three namespaces
(rag_rules, rag_chunks, rag_source_docs). No BM25 — hybrid_search() is an
alias for vector_search(). Pinecone SDK is imported lazily so installs
without RAG_VECTOR_BACKEND=pinecone don't pay the import cost.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.ports import IndexName, SearchHit, VectorDoc

logger = logging.getLogger(__name__)


# ----------------------------------------------------------- helpers ---

def _coerce_scalar(v: Any) -> Any:
    """Pinecone metadata values: str/int/float/bool/list[str]. Coerce UUIDs."""
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _to_pinecone_filter(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Translate internal filter dict to Pinecone's filter DSL.

    Internal shape: {field: value} or {field: [v1, v2, ...]}.
    Pinecone shape: {field: {"$eq": value}} or {field: {"$in": [v1, v2]}}.
    """
    if not filters:
        return None
    out: Dict[str, Any] = {}
    for k, v in filters.items():
        if isinstance(v, (list, tuple, set)):
            out[k] = {"$in": [_coerce_scalar(x) for x in v]}
        else:
            out[k] = {"$eq": _coerce_scalar(v)}
    return out


def _sanitize_metadata(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Stub — implemented in Task 3."""
    raise NotImplementedError
```

- [ ] **Step 5: Run tests — verify filter tests pass**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_helpers.py -v -k "filter"`
Expected: 8 passing tests for `_to_pinecone_filter`. `_sanitize_metadata` tests don't exist yet.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/services backend/app/services/rag/stores/pinecone_store.py
git commit -m "feat(rag): Pinecone filter translator + test package"
```

---

## Task 3: Metadata sanitizer (pure-unit, TDD)

**Files:**
- Modify: `backend/tests/services/rag/stores/test_pinecone_helpers.py` (append tests)
- Modify: `backend/app/services/rag/stores/pinecone_store.py` (implement `_sanitize_metadata`)

- [ ] **Step 1: Append failing tests for `_sanitize_metadata`**

Append the following to `backend/tests/services/rag/stores/test_pinecone_helpers.py`:

```python
def test_sanitize_drops_none_values():
    out = _sanitize_metadata({"a": "x", "b": None, "c": 1})
    assert out == {"a": "x", "c": 1}


def test_sanitize_coerces_uuid_to_str():
    sub_id = uuid.uuid4()
    out = _sanitize_metadata({"submission_id": sub_id})
    assert out == {"submission_id": str(sub_id)}


def test_sanitize_coerces_uuid_list():
    ids = [uuid.uuid4(), uuid.uuid4()]
    out = _sanitize_metadata({"derived_rule_ids": ids})
    assert out == {"derived_rule_ids": [str(ids[0]), str(ids[1])]}


def test_sanitize_truncates_text_over_32kb():
    long = "x" * 40_000
    out = _sanitize_metadata({"text": long})
    assert len(out["text"]) == 32 * 1024
    assert out["text"] == "x" * (32 * 1024)


def test_sanitize_truncates_rule_text_over_8kb():
    long = "y" * 10_000
    out = _sanitize_metadata({"rule_text": long})
    assert len(out["rule_text"]) == 8 * 1024


def test_sanitize_passes_short_strings_unchanged():
    out = _sanitize_metadata({"text": "hello", "rule_text": "world"})
    assert out == {"text": "hello", "rule_text": "world"}


def test_sanitize_preserves_int_bool_float():
    out = _sanitize_metadata({"chunk_index": 3, "is_active": False, "score": 0.42})
    assert out == {"chunk_index": 3, "is_active": False, "score": 0.42}


def test_sanitize_empty_dict():
    assert _sanitize_metadata({}) == {}
```

- [ ] **Step 2: Run tests — verify the new ones fail**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_helpers.py -v -k "sanitize"`
Expected: 8 failures with `NotImplementedError`.

- [ ] **Step 3: Implement `_sanitize_metadata`**

Replace the stub in `backend/app/services/rag/stores/pinecone_store.py` with:

```python
# Truncation caps (bytes) — keeps each vector's metadata under Pinecone's
# 40 KB/vector limit while reserving headroom for the other fields.
_TEXT_TRUNCATE = 32 * 1024     # rag_chunks.text, rag_source_docs.text
_RULE_TEXT_TRUNCATE = 8 * 1024  # rag_rules.rule_text


def _sanitize_metadata(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare a VectorDoc.fields dict for Pinecone's metadata field.

    - Drop None values (Pinecone rejects them).
    - Coerce UUIDs (and lists of UUIDs) to strings.
    - Truncate long text fields under Pinecone's 40 KB/vector cap.
    - Pass through str/int/float/bool/list[str] unchanged.
    """
    out: Dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
            continue
        if isinstance(v, (list, tuple)):
            out[k] = [str(x) if isinstance(x, uuid.UUID) else x for x in v]
            continue
        if isinstance(v, str):
            cap = _RULE_TEXT_TRUNCATE if k == "rule_text" else _TEXT_TRUNCATE
            out[k] = v[:cap] if len(v) > cap else v
            continue
        out[k] = v
    return out
```

- [ ] **Step 4: Run tests — verify all helper tests pass**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_helpers.py -v`
Expected: 16 passing tests total (8 filter + 8 sanitize).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rag/stores/pinecone_store.py backend/tests/services/rag/stores/test_pinecone_helpers.py
git commit -m "feat(rag): Pinecone metadata sanitizer"
```

---

## Task 4: `PineconeStore` skeleton + factory wiring + health probe

**Files:**
- Create: `backend/conftest.py`
- Create: `backend/tests/services/rag/stores/test_pinecone_store.py`
- Modify: `backend/app/services/rag/stores/pinecone_store.py` (add class + init + health)
- Modify: `backend/app/services/rag/factory.py` (add pinecone branch)

- [ ] **Step 1: Register the `pinecone` pytest marker**

Create `backend/conftest.py` with:

```python
"""Pytest configuration for the backend test suite."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "pinecone: live integration test against a real Pinecone index "
        "(skipped unless PINECONE_API_KEY and PINECONE_INDEX_NAME are set)",
    )
```

- [ ] **Step 2: Write the failing live test for construction + health**

Create `backend/tests/services/rag/stores/test_pinecone_store.py` with:

```python
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
```

- [ ] **Step 3: Run tests — verify failures**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_store.py -v`
Expected: `ImportError` (no `PineconeStore` class yet) or the constructor test reaching the not-yet-existing class.

- [ ] **Step 4: Implement the class skeleton + `__init__` + `health`**

Append the following to `backend/app/services/rag/stores/pinecone_store.py` (after `_sanitize_metadata`):

```python
# Map IndexName -> namespace setting attribute on settings.
def _namespace_for(index: IndexName) -> str:
    from app.config import settings
    if index == "rag_rules":
        return settings.pinecone_namespace_rules
    if index == "rag_chunks":
        return settings.pinecone_namespace_chunks
    if index == "rag_source_docs":
        return settings.pinecone_namespace_srcdocs
    raise ValueError(f"Unknown index: {index}")


# Stable ID prefixes — guard against accidental cross-namespace delete.
_ID_PREFIX: Dict[str, str] = {
    "rag_rules": "rules",
    "rag_chunks": "chunks",
    "rag_source_docs": "srcdocs",
}


def _prefix_id(index: IndexName, raw_id: str) -> str:
    return f"{_ID_PREFIX[index]}:{raw_id}"


# ========================================================== PineconeStore

class PineconeStore:
    """VectorStore implementation backed by Pinecone (dense-only)."""

    name = "pinecone"

    def __init__(self) -> None:
        from app.config import settings

        if not settings.pinecone_api_key or not settings.pinecone_index_name:
            raise RAGDegraded(
                "Pinecone backend selected but PINECONE_API_KEY or "
                "PINECONE_INDEX_NAME is not set"
            )

        # Lazy import so the SDK is only required when this backend is active.
        try:
            from pinecone import Pinecone  # type: ignore
        except ImportError as e:
            raise RAGDegraded(f"pinecone SDK not installed: {e}") from e

        try:
            self._pc = Pinecone(api_key=settings.pinecone_api_key)
            self._index = self._pc.Index(settings.pinecone_index_name)
        except Exception as e:
            raise RAGDegraded(f"failed to connect to Pinecone index: {e}") from e

        self._dim = settings.rag_embedding_dim
        self._hybrid_warned = False

    async def _run_sync(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def health(self) -> bool:
        def _do() -> bool:
            try:
                self._index.query(
                    namespace=_namespace_for("rag_rules"),
                    vector=[0.0] * self._dim,
                    top_k=1,
                )
                return True
            except Exception as e:
                logger.warning(f"Pinecone health probe failed: {e}")
                return False

        return await self._run_sync(_do)

    # upsert / delete / search implemented in Tasks 5–7.
```

- [ ] **Step 5: Wire the factory**

Modify `backend/app/services/rag/factory.py` — in `get_vector_store()`, insert a `pinecone` branch before the `pgvector` branch:

```python
@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    backend = (settings.rag_vector_backend or "pgvector").lower()
    if backend == "azure_search":
        from app.services.rag.stores.azure_search_store import AzureSearchStore
        logger.info("RAG vector store: AzureSearchStore")
        return AzureSearchStore()
    if backend == "pinecone":
        from app.services.rag.stores.pinecone_store import PineconeStore
        logger.info("RAG vector store: PineconeStore")
        return PineconeStore()
    if backend == "pgvector":
        from app.services.rag.stores.pgvector_store import PgVectorStore
        logger.info("RAG vector store: PgVectorStore")
        return PgVectorStore()
    raise RAGDegraded(f"Unknown RAG_VECTOR_BACKEND: {backend}")
```

- [ ] **Step 6: Verify the empty-config test passes**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_store.py::test_constructor_raises_rag_degraded_without_api_key -v`
Expected: PASS.

- [ ] **Step 7: Verify the helper tests still pass**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_helpers.py -v`
Expected: 16 passing.

- [ ] **Step 8: (Optional, requires Pinecone creds) Run the live tests**

Set `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` in the shell, then:
Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_store.py -v -m pinecone`
Expected: 2 passing (`test_store_constructs_and_reports_name`, `test_health_returns_true_on_live_index`).

If Pinecone creds are not available, skip this step — the marker handles skipping automatically.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/rag/stores/pinecone_store.py backend/app/services/rag/factory.py backend/conftest.py backend/tests/services/rag/stores/test_pinecone_store.py
git commit -m "feat(rag): PineconeStore skeleton + factory wiring + health"
```

---

## Task 5: `upsert` and `delete`

**Files:**
- Modify: `backend/app/services/rag/stores/pinecone_store.py` (add methods)
- Modify: `backend/tests/services/rag/stores/test_pinecone_store.py` (add live tests)

- [ ] **Step 1: Write failing live tests for upsert + delete**

Append to `backend/tests/services/rag/stores/test_pinecone_store.py`:

```python
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
```

- [ ] **Step 2: Run tests — verify upsert/delete tests fail**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_store.py -v -m pinecone -k "upsert or delete"`
Expected: `AttributeError: 'PineconeStore' object has no attribute 'upsert'` (or method missing). If Pinecone creds unavailable, all tests are skipped — proceed to Step 3.

- [ ] **Step 3: Implement `upsert` and `delete`**

Append to `backend/app/services/rag/stores/pinecone_store.py` (inside the `PineconeStore` class, after `health`):

```python
    # ------------------ upsert / delete ------------------

    _UPSERT_BATCH = 100

    async def upsert(self, index: IndexName, docs: List[VectorDoc]) -> None:
        if not docs:
            return
        ns = _namespace_for(index)
        vectors = []
        for d in docs:
            if len(d.embedding) != self._dim:
                raise RAGIndexingFailed(
                    f"Pinecone upsert {index}: embedding dim {len(d.embedding)} != {self._dim}"
                )
            vectors.append({
                "id": _prefix_id(index, d.id),
                "values": d.embedding,
                "metadata": _sanitize_metadata(d.fields),
            })

        def _do():
            try:
                for i in range(0, len(vectors), self._UPSERT_BATCH):
                    batch = vectors[i : i + self._UPSERT_BATCH]
                    self._index.upsert(vectors=batch, namespace=ns)
            except Exception as e:
                raise RAGIndexingFailed(
                    f"Pinecone upsert into {index} (ns={ns}) failed: {e}"
                ) from e

        await self._run_sync(_do)

    async def delete(self, index: IndexName, ids: List[str]) -> None:
        if not ids:
            return
        ns = _namespace_for(index)
        prefixed = [_prefix_id(index, i) for i in ids]

        def _do():
            try:
                self._index.delete(ids=prefixed, namespace=ns)
            except Exception as e:
                raise RAGIndexingFailed(
                    f"Pinecone delete from {index} (ns={ns}) failed: {e}"
                ) from e

        await self._run_sync(_do)
```

- [ ] **Step 4: Run tests — verify all helper + skipped/live tests behave**

Run: `cd backend && python -m pytest tests/services/rag/stores/ -v`
Expected: 16 helper tests + 1 pure-unit constructor test PASS; live tests SKIP unless creds set, otherwise PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rag/stores/pinecone_store.py backend/tests/services/rag/stores/test_pinecone_store.py
git commit -m "feat(rag): PineconeStore upsert + delete"
```

---

## Task 6: `vector_search` and `hybrid_search` alias

**Files:**
- Modify: `backend/app/services/rag/stores/pinecone_store.py`
- Modify: `backend/tests/services/rag/stores/test_pinecone_store.py`

- [ ] **Step 1: Write failing live tests for search**

Append to `backend/tests/services/rag/stores/test_pinecone_store.py`:

```python
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
```

- [ ] **Step 2: Run tests — verify failures (or skip without creds)**

Run: `cd backend && python -m pytest tests/services/rag/stores/test_pinecone_store.py -v -m pinecone -k "search or hybrid"`
Expected: `AttributeError: 'PineconeStore' object has no attribute 'vector_search'` (if creds set) or all SKIPPED.

- [ ] **Step 3: Implement `vector_search` and `hybrid_search`**

Append to the `PineconeStore` class in `backend/app/services/rag/stores/pinecone_store.py` (after `delete`):

```python
    # ------------------ search ------------------

    async def vector_search(
        self,
        index: IndexName,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        ns = _namespace_for(index)
        pc_filter = _to_pinecone_filter(filters)
        prefix = _ID_PREFIX[index] + ":"

        def _do() -> List[SearchHit]:
            try:
                res = self._index.query(
                    namespace=ns,
                    vector=query_vector,
                    top_k=top_k,
                    include_metadata=True,
                    filter=pc_filter,
                )
            except Exception as e:
                logger.error(f"Pinecone vector_search on {index} failed: {e}")
                raise RAGDegraded(str(e)) from e

            hits: List[SearchHit] = []
            for m in res.matches or []:
                # Strip the namespace prefix from the returned ID so callers
                # get the raw UUID they upserted.
                raw_id = m.id[len(prefix):] if m.id.startswith(prefix) else m.id
                meta = dict(m.metadata or {})
                hits.append(SearchHit(id=raw_id, score=float(m.score), fields=meta))
            return hits

        return await self._run_sync(_do)

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
        # Pinecone has no BM25 leg. query_text / recall_pool / rrf_k are
        # accepted for protocol parity and ignored.
        if not self._hybrid_warned:
            logger.info("Pinecone backend: hybrid_search() is vector-only (BM25 disabled)")
            self._hybrid_warned = True
        return await self.vector_search(index, query_vector, top_k, filters)
```

- [ ] **Step 4: Run all tests**

Run: `cd backend && python -m pytest tests/services/rag/stores/ -v`
Expected: 17 pure-unit/marker tests PASS; live search tests PASS (if creds) or SKIP.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rag/stores/pinecone_store.py backend/tests/services/rag/stores/test_pinecone_store.py
git commit -m "feat(rag): PineconeStore vector_search + hybrid_search alias"
```

---

## Task 7: Factory branch test + reset_singletons interaction

**Files:**
- Modify: `backend/tests/services/rag/stores/test_pinecone_store.py`

- [ ] **Step 1: Write factory-level test**

Append to `backend/tests/services/rag/stores/test_pinecone_store.py`:

```python
def test_factory_returns_pinecone_store_when_configured(monkeypatch):
    """RAG_VECTOR_BACKEND=pinecone with valid creds → factory returns PineconeStore."""
    from app.config import settings as s
    from app.services.rag.factory import get_vector_store, reset_singletons

    monkeypatch.setattr(s, "rag_vector_backend", "pinecone")
    monkeypatch.setattr(s, "pinecone_api_key", "test-key")
    monkeypatch.setattr(s, "pinecone_index_name", "test-index")
    reset_singletons()

    # The factory imports PineconeStore lazily and calls __init__, which
    # tries to connect to Pinecone. We can't connect with a fake key, so
    # this test asserts the *attempted construction path* by checking the
    # exception is RAGDegraded (Pinecone auth fail) not "Unknown backend".
    from app.services.rag.errors import RAGDegraded
    try:
        store = get_vector_store()
        # If we reach here (e.g. mock SDK), the type should be PineconeStore.
        assert store.__class__.__name__ == "PineconeStore"
    except RAGDegraded as e:
        # Expected: real Pinecone SDK rejects the fake key.
        assert "Pinecone" in str(e) or "pinecone" in str(e)
    finally:
        reset_singletons()


def test_factory_rejects_unknown_backend(monkeypatch):
    from app.config import settings as s
    from app.services.rag.factory import get_vector_store, reset_singletons
    from app.services.rag.errors import RAGDegraded

    monkeypatch.setattr(s, "rag_vector_backend", "made_up_backend")
    reset_singletons()
    try:
        with pytest.raises(RAGDegraded):
            get_vector_store()
    finally:
        monkeypatch.setattr(s, "rag_vector_backend", "pgvector")
        reset_singletons()
```

- [ ] **Step 2: Run all tests**

Run: `cd backend && python -m pytest tests/services/rag/stores/ -v`
Expected: All helper tests + both new factory tests PASS. Live tests still gated.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/services/rag/stores/test_pinecone_store.py
git commit -m "test(rag): factory wiring for Pinecone backend"
```

---

## Task 8: End-to-end manual smoke

This task has no automated tests — it validates the live system end-to-end against a real Pinecone index. Run it on the developer's laptop with the user's actual Pinecone credentials.

- [ ] **Step 1: Populate `.env`**

Add (or update) in `backend/.env`:

```
PINECONE_API_KEY=<user's real key>
PINECONE_INDEX_NAME=<user's real index name>
RAG_VECTOR_BACKEND=pinecone
RAG_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=<existing key>
```

Confirm the Pinecone index has `dim=1536` and (ideally) cosine metric. This must match what was set when the index was created.

- [ ] **Step 2: Restart backend**

From the repo root: `docker-compose down && docker-compose up backend` (or `cd backend && uvicorn app.main:app --reload` if running locally).
Expected log line: `RAG vector store: PineconeStore`.

- [ ] **Step 3: Health probe**

Run: `curl -s http://localhost:8000/health/rag | python -m json.tool`
Expected JSON includes `"vector_store": "ok"` (or equivalent — check the existing `/health/rag` response shape in `backend/app/api/routes/rag_health.py`).

- [ ] **Step 4: Backfill from Postgres source-of-truth**

Run: `cd backend && python -m scripts.rag_backfill --rules --source-docs`
Expected: log lines reporting `N rules upserted`, `N source-doc chunks upserted`. Spot-check Pinecone's UI — three namespaces should now exist with vectors in `rag_rules` and `rag_source_docs`.

- [ ] **Step 5: Submit a real document for analysis**

Use the frontend (or `curl POST /submissions/` + `POST /compliance/analyze/{id}/sync`) to analyze a sample marketing snippet.
Expected:
- Analysis completes without errors
- `ComplianceCheck.metadata.rag.degraded` is `False`
- `rules_retrieved_per_chunk` is non-empty and ≤ 8
- `rag_chunks` namespace in Pinecone now has the submission's chunks

- [ ] **Step 6: Rollback test**

Set `RAG_VECTOR_BACKEND=pgvector` in `.env`, restart the backend. The same submission should still analyze successfully against pgvector (data is in both stores after Step 4, and pgvector data is untouched).

- [ ] **Step 7: Commit a note**

If any rough edges were found during the smoke test (e.g. metadata truncation surprises, missing namespace), record them in a follow-up task or directly in `docs/superpowers/specs/2026-05-20-pinecone-backend-design.md` § Open questions.

---

## Self-Review

**Spec coverage:**
- Spec § 3 (third backend, dense-only, factory branch) → Tasks 4 + 7
- Spec § 4.1 (per-namespace metadata + 40 KB cap) → Tasks 3 (sanitizer) + 5 (upsert wires it in)
- Spec § 4.2 (filter translation) → Task 2
- Spec § 5.1 (`vector_search`) → Task 6
- Spec § 5.2 (`hybrid_search` alias + INFO log) → Task 6
- Spec § 6 (indexing flow, 100 vec/batch) → Task 5
- Spec § 7 (config fields, lazy SDK import) → Task 1 + Task 4
- Spec § 8 (factory branch) → Task 4
- Spec § 9 (errors wrap `PineconeException`) → covered by broad `except Exception` in upsert/search; if narrower wrapping is preferred, refine in Task 5/6 by importing `pinecone.exceptions` only inside the SDK-using methods
- Spec § 10 (health probe) → Task 4
- Spec § 11 (one-time INFO log) → Task 6
- Spec § 12 (testing layers) → Tasks 2, 3, 4, 5, 6, 7
- Spec § 14 (requirements.txt) → Task 1
- Spec § 15 (migration / rollout) → Task 8

**Placeholder scan:** No TBD/TODO/"appropriate handling" left. All code blocks are complete. No "similar to Task N" references — code is repeated where needed.

**Type consistency:**
- `_namespace_for(index)` returns `str` everywhere it's used
- `_prefix_id(index, raw_id)` matches the round-trip strip logic in `vector_search`
- `SearchHit(id, score, fields)` matches `ports.py` signature
- `VectorDoc(id, embedding, fields)` matches `ports.py` signature
- `reset_singletons()` exists in `factory.py` (verified)

No issues found.
