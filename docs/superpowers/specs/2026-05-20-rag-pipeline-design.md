# RAG Pipeline — Design Spec

**Date:** 2026-05-20
**Owner:** ai.marketing@bajajlife.com
**Status:** Awaiting user sign-off
**Builds on:** [2026-05-17-regulatory-compliance-agent-design.md](2026-05-17-regulatory-compliance-agent-design.md)

---

## 1. Purpose

Add a Retrieval-Augmented Generation layer to the Regulatory Compliance Agent that powers four use cases:

1. **Rule retrieval for analysis** — replace the current "load all ~65 active rules into every chunk's prompt" pattern with per-chunk semantic + keyword retrieval of the top-K most relevant rules.
2. **Context for chat** — give the `/chat` endpoint rich retrieved context: the rules most relevant to the user's question, the most relevant chunks of the current submission, and matching regulator source passages.
3. **Source-doc retrieval for the rule generator** — when a regulator PDF is ingested, index its chunks so generated rules can carry citations back to the exact passage they came from, and chat can quote that passage verbatim.
4. **Cross-submission similarity search** — surface past analyzed submissions whose content resembles a new submission so reviewers can see precedent.

This spec inherits all decisions from the parent design doc and is **additive** — no existing route or schema is renamed, no existing behavior is removed. The 5-node LangGraph workflow stays; only `dispatch_node` changes its rule-loading strategy.

## 2. Non-goals (v1 of RAG)

- Replacing the existing LLM (Groq stays). RAG is about retrieval, not generation.
- New public REST routes other than `GET /submissions/{id}/similar`, `GET /health/rag`, and an optional `POST /debug/rag/search`.
- Replacing the current `ContentChunk`, `Rule`, or related models. RAG tables are derived/searchable views.
- Hot index updates for SLA. Indexing failures degrade gracefully; backfill CLI catches drift.
- Fine-tuning embeddings. Off-the-shelf `text-embedding-3-small` (1536-dim).

## 3. Architectural decision: pluggable vector backend

The Azure AI Foundry subscription is in-flight but not yet available. The spec ships **two vector-store backends** behind a single interface so the move from local to managed is a config flip, not a rewrite.

| Phase | Backend | Embedder | When |
|---|---|---|---|
| v1 (now) | **pgvector** in existing Postgres | OpenAI `text-embedding-3-small` (direct API) | Until Azure subscription lands |
| v2 (next) | **Azure AI Search** | Azure OpenAI `text-embedding-3-small` | After subscription |

Both speak the same SDK shape (OpenAI-compatible for embeddings) and the same internal `VectorStore` protocol (defined below). Switching is a single env-var change plus a one-time backfill run.

**Quality delta:** pgvector backend does hybrid (vector + Postgres `tsvector` BM25) with Python-side Reciprocal Rank Fusion. Azure AI Search adds a semantic L2 re-ranker on top — empirically ~10-15% precision lift on regulatory text. The re-ranker turns on automatically when the env var is flipped.

## 4. Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│  FastAPI backend (existing tree)                                      │
│                                                                       │
│  ┌─ services/rag/ ──────────────────────────────────────────────┐    │
│  │  ports.py            Protocols                               │    │
│  │     Embedder.embed(texts) -> List[List[float]]               │    │
│  │     VectorStore.upsert / delete / hybrid_search              │    │
│  │                                                              │    │
│  │  embedders/                                                  │    │
│  │     openai_embedder.py    OpenAI text-embedding-3-small      │    │
│  │     azure_embedder.py     Azure OpenAI (same model)          │    │
│  │                                                              │    │
│  │  stores/                                                     │    │
│  │     pgvector_store.py      ◀── v1 default                    │    │
│  │     azure_search_store.py  ◀── v2                            │    │
│  │                                                              │    │
│  │  indexers/      (backend-agnostic — store.upsert)            │    │
│  │     rules_indexer.py                                         │    │
│  │     chunks_indexer.py    (handles current + past via status) │    │
│  │     source_docs_indexer.py                                   │    │
│  │                                                              │    │
│  │  retrievers/    (backend-agnostic — store.hybrid_search)     │    │
│  │     rules_retriever.py                                       │    │
│  │     chat_retriever.py                                        │    │
│  │     source_docs_retriever.py                                 │    │
│  │     similar_subs_retriever.py                                │    │
│  │                                                              │    │
│  │  factory.py    selects concrete impls from settings          │    │
│  │  errors.py     RAGDegraded / RAGIndexingFailed / ...         │    │
│  │  rrf.py        Reciprocal Rank Fusion helper                 │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  Plug-in points (existing files modified):                            │
│    services/agents/graph/nodes.py:dispatch_node  → rules_retriever   │
│    services/agents/graph/nodes.py:preprocess_node→ chunks_indexer    │
│    api/routes/chat.py                            → chat_retriever    │
│    services/rule_generator_service.py            → source_docs_*     │
│  New routes:                                                          │
│    api/routes/similar.py    GET /submissions/{id}/similar             │
│    api/routes/health.py     GET /health/rag                           │
│    api/routes/debug.py      POST /debug/rag/search  (optional)        │
└───────────────────────────────────────────────────────────────────────┘
```

**Config (env-driven):**

```
RAG_EMBEDDING_PROVIDER   openai | azure_openai          (default: openai)
RAG_VECTOR_BACKEND       pgvector | azure_search        (default: pgvector)
RAG_EMBEDDING_MODEL      text-embedding-3-small         (fixed for v1, 1536-dim)
RAG_TOP_K_ANALYSIS       8
RAG_TOP_K_CHAT           5
RAG_TOP_K_SIMILAR        3
RAG_SCORE_THRESHOLD      0.65
RAG_RECALL_POOL          30                              (per-leg recall before fusion)
RAG_RRF_K                60                              (RRF constant)

OPENAI_API_KEY           ...                             (v1)
AZURE_OPENAI_ENDPOINT    https://....openai.azure.com    (v2)
AZURE_OPENAI_API_KEY     ...                             (v2)
AZURE_OPENAI_EMBED_DEPLOYMENT  text-embedding-3-small    (v2 deployment name)
AZURE_SEARCH_ENDPOINT    https://....search.windows.net  (v2)
AZURE_SEARCH_API_KEY     ...                             (v2)
```

## 5. Storage schema

Four logical indexes collapse to **three physical tables / indexes** because submission chunks and past-submission chunks share storage and are filtered at query time.

### 5.1 `rag_rules`

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | = `rules.id` |
| category | text | filterable (irdai, brand, sebi) |
| severity | text | filterable (critical/high/medium/low) |
| is_active | bool | filterable |
| rule_text | text | keyword target |
| keywords | text[] | keyword target |
| embed_text | text | what we actually embedded |
| embedding | vector(1536) | pgvector / Azure: Collection(Edm.Single) |
| search_tsv | tsvector | pgvector only — GIN index |
| source | text | e.g. "seed/irdai-2024-q1" |
| updated_at | timestamptz | |

**`embed_text` recipe:** `f"[{category}] [{severity}] {rule_text}. Keywords: {', '.join(keywords)}"`

### 5.2 `rag_chunks`  (current + past submissions, one table)

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | = `content_chunks.id` |
| submission_id | UUID | filterable; excluded from similarity for same-submission |
| chunk_index | int | |
| page_number | int? | |
| text | text | embedded as-is |
| embedding | vector(1536) | |
| search_tsv | tsvector | pgvector only |
| submission_status | text | filterable (analyzed / analyzing / failed) |
| submission_summary | text | one-line summary, for chat context |
| updated_at | timestamptz | |

### 5.3 `rag_source_docs`

| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| document_id | UUID | which source PDF |
| document_title | text | |
| regulator | text | filterable (irdai / sebi / bajaj_brand) |
| chunk_index | int | |
| page_number | int? | |
| text | text | |
| embedding | vector(1536) | |
| search_tsv | tsvector | |
| derived_rule_ids | uuid[] | reverse pointer for citations |
| uploaded_at | timestamptz | |

### 5.4 Indexes

- pgvector: `IVFFLAT (embedding vector_cosine_ops) WITH (lists=100)` on each `embedding` column, plus `GIN (search_tsv)` on each tsvector.
- Azure AI Search: HNSW (default) on `embedding`, semantic config `rules-semantic` / `chunks-semantic` / `source-docs-semantic`.

### 5.5 Alembic migration

One migration `0002_rag_tables.py`:

1. `CREATE EXTENSION IF NOT EXISTS vector;`
2. Create the three tables.
3. Create IVFFLAT and GIN indexes.
4. Add tsvector triggers (auto-populate `search_tsv` from `text` + relevant fields on insert/update).

## 6. Ingestion triggers

| Event | Table | How |
|---|---|---|
| `scripts/seed_rules.py` finishes | `rag_rules` | Calls `rules_indexer.upsert_all_active_rules()` at end |
| `POST /rules/` or `PATCH /rules/{id}` | `rag_rules` | Route handler awaits `rules_indexer.upsert(rule_id)` after DB commit |
| `DELETE /rules/{id}` or `is_active=false` | `rag_rules` | `rules_indexer.delete(rule_id)` |
| `preprocess_node` (after chunks land in `content_chunks`) | `rag_chunks` | Node calls `chunks_indexer.upsert_for_submission(id, status='analyzing')` |
| `compliance_engine.persist_results` succeeds | `rag_chunks` | `chunks_indexer.update_status(submission_id, 'analyzed', summary)` |
| `POST /rules/generate-from-document` | `rag_source_docs` | Rule generator chunks PDF → embeds → upserts → runs LLM extraction → backfills `derived_rule_ids` per passage |

**Backfill CLI:** `python -m scripts.rag_backfill [--rules] [--chunks] [--source-docs]` — idempotent re-embed and re-upsert from Postgres source-of-truth. Used after schema changes, embedding-model upgrades, first-time setup, or backend swap (pgvector → Azure).

**Ordering & atomicity:** indexer calls happen *after* the DB commit but inside the same async handler. Indexing failure logs and raises `RAGIndexingFailed`, but the user-facing DB write is **not** rolled back. RAG is a derived view; the backfill CLI is the safety net.

## 7. Retrieval flow

### 7.1 Per-chunk rule retrieval in `dispatch_node`

The hottest change. Today: state.active_rules = `{category: [all rules]}`. After: state.active_rules = `{chunk_id: {category: [top-K rules]}}`. `analysis_node` reads `state["active_rules"][chunk_id][category]` per task.

Concurrency: N chunks × M categories with `asyncio.gather`. Typical 6-chunk × 3-category submission = 18 queries, <300ms on both backends.

Fallback: `RAGDegraded` → call existing `load_all_active_rules_grouped(categories)` and set `metadata.rag_degraded=true`.

### 7.2 Per-backend query shape

**pgvector** — two SQL legs in parallel, fused in Python:

```sql
-- Vector leg
SELECT id, 1 - (embedding <=> $emb::vector) AS score
FROM rag_rules
WHERE category = $cat AND is_active = TRUE
ORDER BY embedding <=> $emb::vector
LIMIT $recall_pool;

-- Keyword leg
SELECT id, ts_rank_cd(search_tsv, plainto_tsquery('english', $q)) AS score
FROM rag_rules
WHERE category = $cat AND is_active = TRUE
  AND search_tsv @@ plainto_tsquery('english', $q)
ORDER BY score DESC
LIMIT $recall_pool;
```

Python RRF: `score(id) = Σ 1 / (rrf_k + rank_in_leg)`. Take top-K with `final_score ≥ threshold`.

**Azure AI Search** — one request:

```jsonc
POST /indexes/rag-rules/docs/search
{
  "search": "<chunk text, first 2000 chars>",
  "vectorQueries": [{ "vector": [...], "k": 30, "fields": "embedding" }],
  "queryType": "semantic",
  "semanticConfiguration": "rules-semantic",
  "filter": "category eq 'irdai' and is_active eq true",
  "top": 8
}
```

Azure does BM25 + vector + RRF + L2 re-rank natively.

### 7.3 Chat retrieval

`chat_retriever.retrieve(query, submission_id, violations, ...)` calls three indexes in parallel:

- `rag_rules` — top-5 rules semantically + keyword matching the user message
- `rag_chunks` filtered to `submission_id=current` — top-3 chunks of the current submission
- `rag_source_docs` filtered to regulators covering the matched rules — top-2 source passages

Plus in-process: filter `violations` to those whose `rule_id` is in the retrieved rules set, so chat surfaces violations that have actually been flagged in *this* submission.

Bundle is formatted into the system prompt; the existing `LLMService.stream_response` handles streaming.

### 7.4 Source-doc citations for chat

When chat asks "explain rule X", the chat_retriever does an extra lookup on `rag_source_docs` filtered to `X = ANY(derived_rule_ids)` and quotes the matching passage verbatim. Quote is rendered in the chat bubble with a `[regulator · doc title · p.N]` footer chip.

### 7.5 Cross-submission similarity

`GET /submissions/{id}/similar`:

1. Embed the current submission's concatenated chunks (or its summary).
2. Query `rag_chunks` with filter `submission_status='analyzed' AND submission_id != current`.
3. Group hits by `submission_id`; for each, return `{submission_id, top_score, matching_chunks: [...top-2]}`.
4. Return top-3 prior submissions.

UI surface (future Report-tab card) is out of scope for this spec — this only enables the API.

## 8. Error handling

| Class | Trigger | Behavior |
|---|---|---|
| `RAGDegraded` | Vector store / embedder unreachable, timeout, 5xx | Log WARNING; fall back to legacy path (all rules for analysis; empty retrieval for chat — chat still works against general LLM knowledge); set `metadata.rag_degraded=true` |
| `RAGIndexingFailed` | Upsert failed but DB write succeeded | Log ERROR; in-memory retry with backoff x3; if still failing, log + move on (backfill CLI catches it) |
| `RAGEmbedFailed` | Embedder 4xx or schema error | Same as `RAGDegraded` |

User-facing DB writes are **never** rolled back due to a RAG-layer failure.

## 9. Observability

- **Structured per-call logs** at `logs/rag.json`: `{ts, retriever, backend, query_chars, latency_ms, n_results, top_score, degraded}`. Keeps `logs/log.json` (LLM logs) clean.
- **`GET /health/rag`** returns `{embedder: ok|fail, vector_store: ok|fail, last_index_at_per_table: {...}}`. Probe = 1-token embed + 0-result store query.
- **Per-analysis metadata** under `ComplianceCheck.metadata.rag`: `{degraded, rules_retrieved_per_chunk: [int], embedding_calls, retrieval_calls, ms_total}`. Surfaces as a footer chip on the Report tab: "RAG: 8 rules/chunk · 240ms · ok".

## 10. Testing

| Layer | What | How |
|---|---|---|
| Embedder | API contract, dim=1536, batch | Recorded fixtures (VCR.py) — no live calls in CI |
| `PgVectorStore` | upsert, vector search, hybrid RRF, filters | Integration against the docker-compose Postgres with 10 fixture rules |
| `AzureSearchStore` | Same contract | Same fixtures; `@pytest.mark.azure`; skipped unless `AZURE_SEARCH_ENDPOINT` set |
| Retrievers | Top-K, threshold, dedupe, fallback | Hit `PgVectorStore` with seed data; assert hand-picked top-K |
| `dispatch_node` | Uses retriever, falls back on `RAGDegraded` | Mocked retriever; assert state shape |
| End-to-end | Submission → analyze → violations cite retrieved rules | New fixture submission in existing test harness |
| Backend-swap (v2 gate) | Identical-or-better top-K on Azure vs pgvector | Run retriever contract tests against both, diff top-K |

## 11. Project structure (target deltas)

```
backend/
├── alembic/versions/
│   └── 0002_rag_tables.py                    (NEW)
├── app/
│   ├── config.py                             (MODIFIED — RAG_* settings)
│   ├── services/
│   │   ├── rag/                              (NEW — entire package)
│   │   │   ├── __init__.py
│   │   │   ├── ports.py
│   │   │   ├── errors.py
│   │   │   ├── factory.py
│   │   │   ├── rrf.py
│   │   │   ├── embedders/
│   │   │   │   ├── openai_embedder.py
│   │   │   │   └── azure_embedder.py
│   │   │   ├── stores/
│   │   │   │   ├── pgvector_store.py
│   │   │   │   └── azure_search_store.py
│   │   │   ├── indexers/
│   │   │   │   ├── rules_indexer.py
│   │   │   │   ├── chunks_indexer.py
│   │   │   │   └── source_docs_indexer.py
│   │   │   └── retrievers/
│   │   │       ├── rules_retriever.py
│   │   │       ├── chat_retriever.py
│   │   │       ├── source_docs_retriever.py
│   │   │       └── similar_subs_retriever.py
│   │   ├── agents/graph/nodes.py             (MODIFIED — dispatch + preprocess)
│   │   └── rule_generator_service.py         (MODIFIED — source-doc indexing)
│   └── api/routes/
│       ├── chat.py                           (MODIFIED — chat_retriever)
│       ├── rules.py                          (MODIFIED — upsert hooks)
│       ├── similar.py                        (NEW)
│       ├── health.py                         (MODIFIED — /health/rag)
│       └── debug.py                          (NEW — /debug/rag/search)
├── scripts/
│   └── rag_backfill.py                       (NEW)
└── requirements.txt                          (MODIFIED — add azure-search-documents)
```

## 12. Requirements.txt deltas

Add:
- `openai>=1.59.5` (already present)
- `azure-search-documents>=11.5.0`  (only loaded when `RAG_VECTOR_BACKEND=azure_search`)

Already-present libs used: `pgvector`, `sqlalchemy`, `tiktoken`, `pydantic`.

## 13. Migration / rollout

1. Apply `0002_rag_tables.py` against existing Postgres.
2. Run `python -m scripts.rag_backfill --rules --source-docs` to populate the new tables from existing rows.
3. Set `RAG_VECTOR_BACKEND=pgvector` and `RAG_EMBEDDING_PROVIDER=openai`, set `OPENAI_API_KEY`.
4. Restart backend; smoke-test `GET /health/rag`.
5. Run a fresh submission; confirm `metadata.rag.degraded=false` and `rules_retrieved_per_chunk` ≤ 8.
6. **Later (when Azure available):** create Azure OpenAI deployment + AI Search service; populate `AZURE_*` env vars; flip `RAG_VECTOR_BACKEND=azure_search` and `RAG_EMBEDDING_PROVIDER=azure_openai`; run `python -m scripts.rag_backfill --rules --chunks --source-docs` to populate Azure indexes; restart.

## 14. Open questions

None blocking. Defaults to resolve during implementation:

- Exact RRF `k` (default 60 — empirically robust)
- Exact recall pool size per leg (default 30)
- Whether `rag_chunks.text` should store the chunk text or a pointer to `content_chunks.text` (default: duplicate text for query latency; ~50% storage overhead is acceptable for v1)

---

**Next step:** invoke `writing-plans` to produce a step-by-step implementation plan.
