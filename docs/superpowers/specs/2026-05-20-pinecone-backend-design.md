# Pinecone Vector Backend — Design Spec

**Date:** 2026-05-20
**Owner:** ai.marketing@bajajlife.com
**Status:** Awaiting user sign-off
**Builds on:** [2026-05-20-rag-pipeline-design.md](2026-05-20-rag-pipeline-design.md)

---

## 1. Purpose

Add Pinecone as a third pluggable vector backend for the Regulatory Compliance Agent's RAG layer, alongside the existing `pgvector` (v1 default) and `azure_search` (v2) stores. The user has an existing Pinecone index; this spec lets the backend connect to it via a single env-var flip (`RAG_VECTOR_BACKEND=pinecone`) without changing any retriever, indexer, or LangGraph node.

This spec is strictly **additive**: pgvector remains the v1 default, Azure remains the v2 target. No existing code paths are removed or renamed.

## 2. Non-goals

- Replacing pgvector or Azure as default backends.
- BM25 / sparse-vector hybrid search on Pinecone. Pinecone backend is **dense-only** for v1.
- Migrating existing data to Pinecone automatically — operator runs `rag_backfill` after flipping the env var.
- Using Pinecone Inference for embeddings. The existing `OpenAIEmbedder` (1536-dim `text-embedding-3-small`) is reused.
- New REST routes. Pinecone is purely a `VectorStore` implementation behind the existing factory.

## 3. Architectural decision: third backend, dense-only

Pinecone implements the same `VectorStore` protocol declared in `backend/app/services/rag/ports.py`. The factory in `backend/app/services/rag/factory.py` adds a `pinecone` branch.

```
backend/app/services/rag/stores/
├── pgvector_store.py        (existing — v1 default, unchanged)
├── azure_search_store.py    (existing — v2 target, unchanged)
└── pinecone_store.py        (NEW)
```

Because Pinecone has no native BM25 keyword leg in its query API, `PineconeStore.hybrid_search()` is an **alias** that delegates to `vector_search()`. Retrievers do not need to know — they call `hybrid_search()` and receive dense top-K results. A one-time INFO log at startup ("Pinecone backend: hybrid search disabled, vector-only") makes the trade-off visible.

| Backend | Hybrid? | Embedder | When |
|---|---|---|---|
| `pgvector`     | vector + tsvector BM25 + RRF | OpenAI 1536 | v1 default |
| `pinecone`     | **vector only** (alias)      | OpenAI 1536 | v1 alt — current spec |
| `azure_search` | vector + BM25 + RRF + L2 re-rank | Azure OpenAI 1536 | v2 |

**Quality trade-off:** dense-only loses precision on pure-keyword queries (acronyms like "ULIP", numeric policy codes). Acceptable for v1 because the compliance rules and chunks are short, semantically rich text. If precision regresses noticeably, flipping back to `pgvector` (which keeps full hybrid) is a one-line env change.

## 4. Storage layout — one Pinecone index, three namespaces

The user's existing Pinecone index is reused. The three logical RAG indexes are mapped to Pinecone **namespaces** (Pinecone-native multi-tenancy primitive — no per-namespace cost, fast filtering).

| Logical index     | Pinecone namespace | Vector ID format       |
|---|---|---|
| `rag_rules`       | `rag_rules`        | `rules:{uuid}`         |
| `rag_chunks`      | `rag_chunks`       | `chunks:{uuid}`        |
| `rag_source_docs` | `rag_source_docs`  | `srcdocs:{uuid}`       |

The ID prefix guards against cross-namespace collisions during deletes and makes log lines greppable.

### 4.1 Metadata stored per vector

Pinecone allows a JSON `metadata` object per vector, capped at **40 KB/vector total**. We store the fields that filters or retrievers need at query time; long text fields are truncated and Postgres remains source-of-truth.

| Namespace | Required metadata fields |
|---|---|
| `rag_rules`       | `category`, `severity`, `is_active`, `rule_text` (≤ 8 KB truncate), `source` |
| `rag_chunks`      | `submission_id`, `chunk_index`, `page_number`, `submission_status`, `submission_summary`, `text` (≤ 32 KB truncate) |
| `rag_source_docs` | `document_id`, `document_title`, `regulator`, `chunk_index`, `page_number`, `text` (≤ 32 KB truncate), `derived_rule_ids` (list of strings) |

`None` values are dropped before upsert (Pinecone rejects null metadata values).

### 4.2 Filter translation

Internal filter dict (used unchanged by retrievers) → Pinecone DSL:

| Internal filter           | Pinecone filter expression           |
|---|---|
| `{"category": "irdai"}`   | `{"category": {"$eq": "irdai"}}`     |
| `{"is_active": True}`     | `{"is_active": {"$eq": True}}`       |
| `{"category": ["irdai", "sebi"]}` | `{"category": {"$in": ["irdai", "sebi"]}}` |

Only equality and `$in` are required because existing retrievers (`similar_subs_retriever.py`, `chat_retriever.py`, `rules_retriever.py`) filter exclusively on those shapes. The pgvector store's whitelist confirms the same — there is no `$ne` use case. If a retriever later needs `$ne`, both stores can be extended in lockstep.

Translation lives in a private `_to_pinecone_filter()` helper inside `pinecone_store.py`.

## 5. Search behavior

### 5.1 `vector_search()`

```python
res = self._index.query(
    namespace=ns_for(index),
    vector=query_vector,
    top_k=top_k,
    include_metadata=True,
    filter=self._to_pinecone_filter(filters) if filters else None,
)
return [SearchHit(id=m.id, score=m.score, fields=dict(m.metadata or {})) for m in res.matches]
```

Cosine similarity is assumed (matches `text-embedding-3-small` convention and is the typical default for new Pinecone indexes). The store does not configure the metric — that's a property of the existing index.

### 5.2 `hybrid_search()`

```python
async def hybrid_search(self, index, query_text, query_vector, top_k, recall_pool, rrf_k, filters=None):
    # Pinecone is dense-only. query_text, recall_pool, rrf_k are accepted for protocol parity but ignored.
    return await self.vector_search(index, query_vector, top_k, filters)
```

A one-time INFO log on first hybrid call per process: `"Pinecone backend: hybrid_search() is vector-only (BM25 disabled)"`.

## 6. Indexing flow

Indexers (`rules_indexer`, `chunks_indexer`, `source_docs_indexer`) are unchanged — they call `store.upsert(index_name, docs)`.

`PineconeStore.upsert()`:

1. Resolves namespace from `index_name`.
2. For each `VectorDoc`, builds a Pinecone vector tuple `{id, values, metadata}`:
   - ID = `f"{prefix}:{doc.id}"` (prefix per namespace, table above)
   - `values` = `doc.embedding` (must be 1536-dim; assert at boundary)
   - `metadata` = `_sanitize_metadata(doc.fields)` (drop Nones, truncate text fields, coerce UUIDs → str)
3. Batches at **100 vectors/request** (Pinecone-recommended), calls `self._index.upsert(vectors=batch, namespace=ns)` per batch.

`PineconeStore.delete()`:

- Translates internal IDs to prefixed form, calls `self._index.delete(ids=prefixed, namespace=ns)`.

Backfill (`python -m scripts.rag_backfill`) works without change — it walks Postgres source-of-truth and calls `store.upsert()`.

## 7. Configuration

New fields in `backend/app/config.py`:

```python
# Pinecone (alternative v1 vector store)
pinecone_api_key: str = ""
pinecone_index_name: str = ""
pinecone_namespace_rules: str = "rag_rules"
pinecone_namespace_chunks: str = "rag_chunks"
pinecone_namespace_srcdocs: str = "rag_source_docs"
```

The `pinecone` SDK (v5+) auto-resolves the index host from `(api_key, index_name)` via `Pinecone(api_key=...).Index(index_name)` — no environment/cloud/region env vars needed.

Operator `.env` additions (user fills in locally):

```
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=compliance-rag
RAG_VECTOR_BACKEND=pinecone
```

`RAG_EMBEDDING_PROVIDER` stays `openai`, `RAG_EMBEDDING_DIM` stays `1536`.

## 8. Factory wiring

`backend/app/services/rag/factory.py` adds a `pinecone` branch:

```python
if backend == "pinecone":
    from app.services.rag.stores.pinecone_store import PineconeStore
    logger.info("RAG vector store: PineconeStore")
    return PineconeStore()
```

`PineconeStore.__init__` lazily imports `pinecone`, reads settings, constructs the client and the index handle. If `PINECONE_API_KEY` or `PINECONE_INDEX_NAME` is empty, raise `RAGDegraded` at construction time so the factory error path surfaces in `GET /health/rag`.

## 9. Errors

| Class | Trigger | Behavior |
|---|---|---|
| `RAGDegraded`        | Pinecone unreachable, auth error, 5xx, missing config at init | Log WARNING; existing fallback path (load all active rules; empty chat retrieval) applies unchanged |
| `RAGIndexingFailed`  | Upsert/delete failed but DB write succeeded | Log ERROR; in-memory retry x3 with backoff; if still failing, log + move on (backfill CLI catches it) |

Wrap `pinecone.exceptions.PineconeException` (and its subclasses `PineconeApiException`, `PineconeProtocolError`) at the boundary; never let raw SDK exceptions escape `PineconeStore`.

DB writes never roll back due to a Pinecone failure.

## 10. Health probe

`PineconeStore.health()`:

```python
self._index.query(namespace=self._ns_rules, vector=[0.0] * self._dim, top_k=1)
return True
```

Returns `True` on success, `False` on any exception. Wired into the existing `GET /health/rag` route via the factory-provided store.

## 11. Observability

Existing `logs/rag.json` structured per-call logging works unchanged (`backend` field will be `"pinecone"`). Per-analysis `ComplianceCheck.metadata.rag` continues to capture `degraded`, `rules_retrieved_per_chunk`, `embedding_calls`, `retrieval_calls`, `ms_total`.

One additional one-time INFO log per process: `"Pinecone backend: hybrid_search() is vector-only (BM25 disabled)"` — emitted on the first call to `hybrid_search()` so the log file records the trade-off without spamming.

## 12. Testing

| Layer | What | How |
|---|---|---|
| `PineconeStore` contract | upsert, vector_search, hybrid_search alias, delete, health, filter translation | New `backend/tests/services/rag/stores/test_pinecone_store.py`, `@pytest.mark.pinecone`, skipped unless `PINECONE_API_KEY` set |
| Filter translator | `_to_pinecone_filter` for all four filter shapes (eq, in, ne, bool) | Pure-unit tests, no network |
| Metadata sanitizer | `_sanitize_metadata` truncation + None drop + UUID coercion | Pure-unit tests |
| Factory | `RAG_VECTOR_BACKEND=pinecone` returns `PineconeStore`; missing key raises `RAGDegraded` | Existing factory tests extended |
| End-to-end | Submission → analyze → violations cite retrieved rules with Pinecone backend | New `@pytest.mark.pinecone` integration test reusing the existing fixture submission |

The existing pgvector and Azure tests are untouched.

## 13. Project structure (deltas only)

```
backend/
├── app/
│   ├── config.py                                            (MODIFIED — pinecone_* fields)
│   └── services/rag/
│       ├── factory.py                                       (MODIFIED — pinecone branch)
│       └── stores/
│           └── pinecone_store.py                            (NEW)
├── tests/services/rag/stores/
│   └── test_pinecone_store.py                               (NEW)
└── requirements.txt                                         (MODIFIED — add pinecone>=5.0.0)
```

## 14. Requirements.txt delta

Add:
- `pinecone>=5.0.0` (loaded only when `RAG_VECTOR_BACKEND=pinecone`)

The deprecated `pinecone-client` package name is **not** used (renamed upstream in v5).

## 15. Migration / rollout

1. Ensure `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` are set in `backend/.env`. Confirm the existing Pinecone index has `dim=1536` and cosine metric (or its default for new serverless indexes).
2. `pip install -r backend/requirements.txt` to pick up `pinecone>=5.0.0`.
3. Set `RAG_VECTOR_BACKEND=pinecone` in `.env`. Keep `RAG_EMBEDDING_PROVIDER=openai`.
4. Restart backend. Hit `GET /health/rag` — confirm `vector_store: ok`.
5. Run `python -m scripts.rag_backfill --rules --source-docs` to populate Pinecone from the existing Postgres source-of-truth. (Chunks will populate naturally as new submissions arrive; add `--chunks` if you want to backfill past submissions immediately.)
6. Run a fresh submission; confirm `metadata.rag.degraded=false` and that rules retrieval works (`rules_retrieved_per_chunk` ≤ 8).
7. **Rollback at any time:** set `RAG_VECTOR_BACKEND=pgvector` and restart — pgvector data is untouched.

## 16. Open questions

None blocking. Defaults to resolve during implementation:

- **Metadata truncation threshold for `text` fields.** Default: 32 KB for chunks/source-docs, 8 KB for rules — leaves headroom under Pinecone's 40 KB/vector cap for the other fields.
- **Batch size for upsert.** Default: 100 vectors/request (Pinecone-recommended).
- **Retry/backoff on `PineconeApiException`.** Default: best-effort retry inside the indexer (3 attempts, exponential backoff starting at 250 ms); align with whatever retry library is already used by the project at implementation time.

---

**Next step:** invoke `writing-plans` to produce a step-by-step implementation plan.
