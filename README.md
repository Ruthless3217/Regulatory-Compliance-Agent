# Regulatory Compliance Agent

An AI-powered regulatory compliance checking tool for Bajaj Allianz Life marketing content. Paste or upload copy, get inline-highlighted violations against IRDAI / SEBI / Bajaj-brand expectations, a shareable report with score + grade, and a chat interface to ask follow-up questions or request compliant rewrites.

The engine grades by **imitating real past reviewer decisions** (precedents) retrieved from a vector knowledge base, not just static rules — see [Precedent Compliance Engine](#precedent-compliance-engine-vector-memory) below.

**Stack:** Next.js 15 + React 19 + TypeScript + Tailwind frontend · FastAPI + LangGraph + Azure OpenAI (gpt-5.4) backend · PostgreSQL (pgvector) + Redis · pluggable RAG (pgvector / Azure AI Search, embeddings via OpenAI / Azure OpenAI / Cohere / Azure-Foundry Cohere).

## Quick Start (docker-compose)

```bash
# 1) Set your LLM key
export LLM_API_KEY=your_google_gemini_api_key

# 2) Start the full stack (postgres+pgvector, redis, backend, frontend, db-backup)
docker-compose up -d

# 3) Open
#   Frontend → http://localhost:3000
#   API docs → http://localhost:8000/docs
```

The backend container runs `alembic upgrade head` before launching uvicorn (see `backend/Dockerfile` CMD), so the schema is migrated on every boot. Then seed the rule corpus and ingest the precedent knowledge base once:

```bash
# Seed starter rules (IRDAI / Bajaj brand / SEBI)
docker exec compliance-backend python -m scripts.seed_rules

# Ingest the reviewer-decision corpus that powers grading
docker exec compliance-backend python -m scripts.ingest_knowledge_base
```

Compose mounts `./dataset` and `./docs` read-only into the backend at `/app/dataset` and `/app/docs`. A `compliance-backup` sidecar dumps Postgres daily (14-day retention).

### Prerequisites (local, without Docker)
- Python 3.11+
- PostgreSQL 15+ with the `pgvector` extension
- Redis 7+ (optional — falls back to an in-memory LangGraph checkpointer)
- Google Gemini API key (OpenAI-compatible endpoint)
- An embeddings provider key (OpenAI by default) for the RAG layer

```bash
cd backend
cp .env.example .env          # add LLM_API_KEY and an embeddings key
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

- **Swagger UI**: http://localhost:8000/docs · **ReDoc**: http://localhost:8000/redoc · **Health**: http://localhost:8000/health

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI REST API                            │
│  /submissions /compliance /rules /dashboard /chat                  │
│  /knowledge-base /health/rag /debug/rag/search                     │
└───────────────────────────┬────────────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────────────┐
│                 ComplianceEngine + ComplianceOrchestrator           │
│                                                                     │
│  LangGraph: START → preprocess → dispatch → analysis → scoring      │
│                                          → refinement → END         │
│                                              ↑                      │
│                                       interrupt_before              │
│                                      (HITL review point)            │
└───────────────────────────┬────────────────────────────────────────┘
                            │
   ┌────────────────────────┼─────────────────────────┐
   ▼                        ▼                          ▼
┌──────────────┐   ┌──────────────────┐    ┌────────────────────┐
│ preprocess   │   │ dispatch         │    │ analysis (per chunk)│
│ (Librarian)  │   │ (Brain)          │    │ (Specialist + Critic)│
│ chunk + index│   │ retrieve rules + │    │ grade vs precedents │
│ → rag_chunks │   │ precedents/chunk │    │ at temperature 0    │
└──────────────┘   └──────────────────┘    └────────────────────┘
        │                   │                          │
        ▼                   ▼                          ▼
┌──────────────┐   ┌──────────────────┐    ┌────────────────────┐
│ pgvector     │   │ rag_rules /      │    │   LLM Service       │
│ rag_chunks   │   │ rag_compliance_  │    │  (Gemini 2.0 Flash) │
│              │   │ examples         │    │                     │
└──────────────┘   └──────────────────┘    └────────────────────┘
```

## LangGraph Workflow

The analysis is a 5-node LangGraph pipeline managed by `ComplianceOrchestrator`
(checkpointed to Redis via `AsyncRedisSaver`, or an in-memory `MemorySaver`
fallback). Order: `preprocess → dispatch → analysis → scoring → refinement`,
with `interrupt_before=["refinement_node"]` for the HITL checkpoint.

1. **preprocess_node** (Librarian): extracts and token-chunks the document, then indexes chunks into the `rag_chunks` vector index. RAG indexing failures are non-fatal.
2. **dispatch_node** (Brain): loads active rules and, per chunk, retrieves the top-K most similar **precedents** from `rag_compliance_examples` plus relevant rules from `rag_rules`. If the knowledge base is empty it marks the run `degraded: "knowledge_base_empty"`; with no chunks, `degraded: "no_content"`.
3. **analysis_node** (Specialist): grades each chunk against its retrieved precedents in parallel (bounded by an `asyncio.Semaphore(8)`) at **temperature 0**, with a one-shot corrective retry if the model's output fails validation. A second **Critic** pass can downgrade/drop low-confidence violations.
4. **scoring_node** (Evaluator): aggregates violations into per-category and overall scores (0–100) and a letter grade.
5. **refinement_node** (HITL): the graph pauses here for human review; resume via `POST /compliance/resume/{id}` with optional feedback.

## Precedent Compliance Engine (Vector Memory)

The agent grades documents by imitating **real past reviewer decisions**, not
just abstract rules. Precedents `(draft chunk → reviewer comment → anchor →
final rewrite → reviewer name → severity)` are mined from the reviewed corpus
and stored in a dedicated pgvector index, `rag_compliance_examples`, alongside
`rag_rules`, `rag_chunks`, and `rag_source_docs`.

**How it differs from `rag_rules`:** rules are abstract policy statements;
precedents are concrete, human-made review decisions used as few-shot examples.
On the analysis path, `dispatch_node` retrieves the top-K most similar
precedents per document chunk and `analysis_node` grades each chunk against them
at temperature 0. The rule CRUD/generation endpoints and rule retrieval remain
fully operational; they are simply no longer the sole analysis driver.

**Vocabulary:** severities `critical | moderate | informational`; categories
`terminology issue | legal language | missing reference | disclaimer issue |
other`. (The scoring service still understands the legacy
`critical/high/medium/low` weights for back-compatibility.)

### Ingest the knowledge base

```bash
# In the backend container (dataset mounted read-only at /app/dataset):
docker exec compliance-backend python -m scripts.ingest_knowledge_base --limit 50 --preview
docker exec compliance-backend python -m scripts.ingest_knowledge_base

# Or on the host (Postgres exposed on localhost:5432):
cd backend && python -m scripts.ingest_knowledge_base --folder ../dataset/Dataset/Dataset/dataset_2.1_rl
```

### Operational note

If the knowledge base is empty, analysis produces **no** violations and the run
metadata carries `degraded: "knowledge_base_empty"` with a prominent log warning
— ingest the corpus to enable grading.

## RAG Layer (pluggable)

The retrieval layer is provider-agnostic, selected entirely via config
(`backend/app/services/rag/factory.py`, `ports.py`):

| Concern | Implementations | Selected by |
|---------|-----------------|-------------|
| **Embedders** | OpenAI, Azure OpenAI, Cohere, Azure-Foundry Cohere (`azure_cohere`, default) | `RAG_EMBEDDING_PROVIDER` |
| **Vector stores** | pgvector (default), Azure AI Search | `RAG_VECTOR_BACKEND` |
| **Indexers** | `rag_chunks`, `rag_rules`, `rag_source_docs`, `rag_compliance_examples` | — |
| **Retrievers** | precedent, rules, chat, similar-submissions, source-docs | — |

The pgvector store runs hybrid search (vector + BM25/tsvector) fused with
Reciprocal Rank Fusion (`rrf.py`); Azure AI Search performs hybrid natively.
Retrieval failures raise `RAGDegraded`/`RAGEmbedFailed`/`RAGIndexingFailed`, and
callers degrade gracefully rather than failing the request.

## Core Components

| Component | Description |
|-----------|-------------|
| `ComplianceOrchestrator` | Builds the LangGraph state machine and manages checkpointing (Redis/memory) |
| `ComplianceEngine` | Entry point for analysis; runs the graph and persists checks + violations |
| `ScoringService` | Calculates compliance scores (0–100) and grades (A–F) from weighted violations |
| `StandardComplianceAgent` | LLM-powered per-category analysis agent (legacy rules path) |
| `CriticAgent` | Second LLM pass that downgrades/drops low-confidence violations |
| `ContextEngineeringService` | Document extraction, token-chunking, and prompt construction (rules + precedent prompts) |
| `LLMService` | Async OpenAI-compatible client with schema-validated structured output and retries |
| `RuleGeneratorService` | Rule CRUD and AI extraction of rules from regulator documents |
| `KnowledgeBaseIngestionService` | Parses the reviewer-decision corpus and upserts precedents into `rag_compliance_examples` |
| `VectorProjectionService` | Projects embeddings to 2-D (UMAP → PCA fallback) for the knowledge-base visualization |

## Frontend

Next.js 15 (App Router) + React 19 + TypeScript + Tailwind 3.4 + Radix-based
shadcn-style primitives, Recharts for charts/scatter, react-markdown for chat.
White + Bajaj-blue palette; Source Serif 4 headlines, Inter body, JetBrains Mono
for numerals. Routes live under the `(workspace)` route group (the group name is
not part of the URL).

### Pages

| Path | What it does |
|------|--------------|
| `/` | Submissions inbox — grouped tables + KPI strip + activity rail |
| `/new` | New analysis — paste / upload / URL, with rule-scope selector |
| `/submissions/[id]` | **Review** tab — inline highlights + ViolationCards |
| `/submissions/[id]/report` | **Report** tab — score hero, KPI strip, grouped violations, print/PDF |
| `/submissions/[id]/chat` | **Chat** tab — streamed Q&A grounded in the submission |
| `/rules` | Rules library — filter, activate/deactivate, inline edit |
| `/rules/generate` | AI rule extraction from regulator PDFs/text |
| `/dashboard` | KPIs + category radar + severity distribution |
| `/knowledge-base` | 2-D vector-space scatter of precedents / rules / source passages, with severity & category filters |
| `/settings` | Density toggle, API base URL, health check, version |

**Sidebar groups:** Workspace (Submissions, New analysis) · Library (Rules,
Generate rules) · Insights (Dashboard, Knowledge base) · Settings.

### Local dev

```bash
cd frontend
npm install
cp .env.example .env.local    # then edit if backend isn't at :8000
npm run dev                    # → http://localhost:3000
```

Scripts: `dev`, `build`, `start`, `lint`, `typecheck` (`tsc --noEmit`).

Frontend env vars (`NEXT_PUBLIC_*`, baked at build time):

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_BASE` | Backend API base URL | `http://localhost:8000` |
| `NEXT_PUBLIC_BUILD_SHA` | Build commit shown in the UI | `dev` |

## API Endpoints

### Submissions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/submissions` | Create submission (text or file upload: PDF/DOCX/HTML/MD/text) |
| GET | `/submissions` | List submissions (`skip`, `limit`) |
| GET | `/submissions/{id}` | Get a submission |
| DELETE | `/submissions/{id}` | Delete a submission and its file |
| GET | `/submissions/{id}/similar` | Top-N similar prior submissions (`top_k`, `chunks_per`) |

### Compliance Analysis
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/compliance/analyze/{id}` | Trigger async analysis (background task) |
| POST | `/compliance/analyze/{id}/sync` | Trigger sync analysis (blocks for result) |
| POST | `/compliance/analyze/{id}/stream` | **SSE** analysis with `stage`/`chunk`/`score`/`done` progress events |
| GET | `/compliance/results/{id}` | Latest results (score, grade, violations) |
| GET | `/compliance/check/{check_id}` | Get a specific check's details |
| POST | `/compliance/resume/{id}` | Resume the HITL workflow (optional `feedback`) |

### Rules Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rules` | Create a rule manually |
| GET | `/rules` | List rules (`category`, `is_active`, `skip`, `limit`) |
| GET | `/rules/{id}` | Get a rule |
| PATCH | `/rules/{id}` | Update a rule (activate/deactivate, severity, text) |
| DELETE | `/rules/{id}` | Delete a rule |
| POST | `/rules/generate-from-document` | AI rule extraction from an uploaded doc or text |

### Chat (all SSE-streamed)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | RAG-grounded Q&A about a submission (`token`/`done` events) |
| POST | `/chat/quote-violation` | Explain why a violation flags a passage |
| POST | `/chat/suggest-rewrite` | Suggest a compliant rewrite of a flagged passage |

### Knowledge Base
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/knowledge-base/ingest` | Ingest precedents `{ folder_path, preview?, limit? }` |
| GET | `/knowledge-base/stats` | Counts by category/severity/reviewer, distinct files |
| GET | `/knowledge-base/projection` | 2-D embedding map (`method=umap\|pca`, `refresh`) |

### Dashboard
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/summary` | Overall stats |
| GET | `/dashboard/violations-by-category` | Violations by category |
| GET | `/dashboard/violations-by-severity` | Violations by severity |

### RAG Health & Debug
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health/rag` | Embedder + vector-store reachability and last-indexed timestamps |
| POST | `/debug/rag/search` | Ad-hoc hybrid search against an index for troubleshooting |

## Scripts

Run with `python -m scripts.<name>` (inside the backend container, prefix with
`docker exec compliance-backend`).

| Script | Key flags | Purpose |
|--------|-----------|---------|
| `seed_rules` | — | Load YAML seed rules and backfill them into `rag_rules` (idempotent) |
| `ingest_knowledge_base` | `--folder`, `--limit`, `--preview` | Parse the reviewer-decision corpus into `rag_compliance_examples` |
| `ingest_guidelines` | `--skip-existing` | LLM-extract rules from `docs/guidelines-docs/` into `rag_rules` + `rag_source_docs` |
| `rag_backfill` | `--rules`, `--chunks`, `--source-docs`, `--all` | Re-embed existing rows after an embedding-model or backend change |
| `eval_precedent_replay` | `--folder`, `--eval-frac 0.1` | Leakage-safe replay (train/eval split by file hash; same-document precedents excluded) → `logs/eval_replay.json` |

## Typical Workflow

1. **Seed & ingest**: `seed_rules` + `ingest_knowledge_base` to populate rules and precedents.
2. **Submit document**: `POST /submissions` with your content.
3. **Run analysis**: `POST /compliance/analyze/{id}/sync` (or `/stream` for progress).
4. **Review results**: `GET /compliance/results/{id}` for violations and scores.

### Example

```bash
# 1. Create a submission
curl -X POST "http://localhost:8000/submissions" \
  -F "title=Insurance Policy Draft" \
  -F "content_type=text" \
  -F "content=This policy provides coverage for life insurance..."

# 2. Analyze it (replace {id} with the submission ID from step 1)
curl -X POST "http://localhost:8000/compliance/analyze/{id}/sync"

# 3. Get results
curl "http://localhost:8000/compliance/results/{id}"
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | Google Gemini API key | **Required** |
| `LLM_BASE_URL` | OpenAI-compatible base URL | `…/v1beta/openai/` |
| `LLM_MODEL` | LLM model | `gemini-2.0-flash` |
| `LLM_INSECURE_TLS` | Bypass TLS verify (e.g. behind SSL inspection) | `false` |
| `DATABASE_URL` | PostgreSQL URL (else built from `DB_*`) | Auto-constructed |
| `DB_USER` / `DB_PASS` / `DB_NAME` / `DB_HOST` / `DB_PORT` | DB components | `compliance_*` / `localhost` / `5432` |
| `REDIS_URL` | Redis URL for LangGraph persistence | `redis://localhost:6379` |
| `MAX_UPLOAD_SIZE` | Max upload bytes | `52428800` (50 MB) |
| `UPLOAD_DIR` | Upload directory | `./uploads` |
| **RAG** | | |
| `RAG_EMBEDDING_PROVIDER` | `openai` \| `azure_openai` \| `cohere` \| `azure_cohere` | `azure_cohere` |
| `RAG_VECTOR_BACKEND` | `pgvector` \| `azure_search` | `pgvector` |
| `RAG_EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| `RAG_EMBEDDING_DIM` | Embedding dimension (1024 for Cohere) | `1536` |
| `RAG_TOP_K_ANALYSIS` / `RAG_TOP_K_CHAT` / `RAG_TOP_K_SIMILAR` | Top-K per use case | `8` / `5` / `3` |
| `RAG_RECALL_POOL` / `RAG_RRF_K` / `RAG_SCORE_THRESHOLD` | Hybrid-search tuning | `30` / `60` / `0.0` |
| **Precedent engine** | | |
| `PGVECTOR_TOP_K` | Precedents retrieved per chunk | `5` |
| `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` / `KB_BATCH_SIZE` | Ingestion chunking/batching | `500` / `50` / `100` |
| `KB_MIN_FUZZY_SCORE` | RapidFuzz anchor-match threshold | `60` |
| `VIZ_POINTS_PER_INDEX` | Projection point cap per index | `2000` |
| **Provider keys** | | |
| `LLM_CLASSIFY_MODEL` | Cheap/fast model for the critic pass (e.g. `gpt-5.4-nano`) | — (falls back to `LLM_MODEL`) |
| `OPENAI_API_KEY` | OpenAI embeddings key | — |
| `AZURE_OPENAI_*` / `AZURE_SEARCH_*` | Azure OpenAI + AI Search config | — |
| `AZURE_INFERENCE_ENDPOINT` / `AZURE_INFERENCE_API_KEY` / `AZURE_COHERE_EMBED_DEPLOYMENT` | Azure AI Foundry Cohere embeddings (`azure_cohere`) | — |
| `COHERE_API_KEY` / `COHERE_EMBEDDING_MODEL` | Cohere embeddings (public API) | `embed-english-v3.0` |
| **Tracing** | | |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | LangSmith tracing | `false` / — / `regulatory-compliance-agent` |

> The backend `Dockerfile` registers Bajaj/Cisco-Umbrella corporate root CAs and
> pre-caches the tiktoken BPE so it works behind the corporate SSL-inspection
> proxy with no outbound calls to public CDNs.

## Design Principles

This implementation follows **12-Factor Agent** principles:
- **Stateless Reducer Pattern**: `State_n+1 = f(State_n, Input)`
- **Small, Focused Agents**: each agent handles exactly one rule category
- **Immutable Observability**: every agent action is traced and logged
- **Parallel Execution**: chunks × categories analyzed concurrently (bounded)
- **Graceful Degradation**: RAG failures downgrade, never crash, the request
