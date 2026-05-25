# Precedent-Driven Compliance Engine — Design Spec

**Date:** 2026-05-25
**Status:** Draft for review
**Author:** Claude Code (migration brief reconciliation)

---

## 1. Context & Problem

The migration brief asks to convert a "brittle rule-based RAG with no vector
infrastructure" into a precedent-learning vector memory engine, building
pgvector + embeddings + similarity retrieval **from scratch**.

**Six parallel read-only analysis agents confirmed the brief's central premise
is false.** The repository already contains a production-grade RAG subsystem:

- pgvector enabled in `alembic/versions/0002_rag_tables.py:27`; three vector
  tables: `rag_rules`, `rag_chunks`, `rag_source_docs`.
- Multi-provider embedders (OpenAI 1536 / Azure / Cohere 1024) behind a
  `get_embedder()` factory (`services/rag/factory.py:19`).
- A `PgVectorStore` with hybrid search (cosine + BM25 + RRF fusion).
- `dispatch_node` **already** does per-chunk hybrid rule retrieval with
  source-quote enrichment (`services/agents/graph/nodes.py:134-172`).
- `SimilarSubmissionsRetriever` already does vector precedent lookup of past
  submissions.
- Docker already runs `pgvector/pgvector:pg15` (`docker-compose.yml:5`).
- Migrations are env-driven on dimension (`VECTOR({EMBED_DIM})`) + IVFFlat.

What does **not** exist: a knowledge base of **real reviewer decisions** from
the 2,000–3,000 reviewed JSON files — i.e. `(draft chunk → reviewer comment →
anchor → final rewrite → reviewer name → severity)` precedents. Ingesting that
corpus and using it for few-shot reviewer imitation is the genuine, additive
core of this migration.

**Therefore:** we implement the brief's *intent* using the *existing*
architecture, not the brief's literal (duplicative, slightly-incompatible)
instructions.

---

## 2. Confirmed current-state facts (load-bearing for this design)

| Fact | Evidence |
|---|---|
| Violation severity/category are free `str`, not enums | `schemas/compliance_schemas.py:7-8` |
| Prompt is built in `ContextEngineeringService.create_compliance_prompts`, not the agent | `services/preprocessing_service.py:294` |
| Analysis loop = `chunk × active_categories`; categories come from `active_rules` keys | `services/agents/graph/nodes.py:219, 315-319` |
| Scoring discovers categories FROM violations; `active_rules` only a fallback | `services/agents/compliance/scoring.py:77-91` |
| Scoring points: `rule.points_deduction` if `rule_id`, else `SEVERITY_WEIGHTS` | `scoring.py:102-141` |
| `SEVERITY_WEIGHTS = {critical:20, high:10, medium:5, low:2}` | `scoring.py:18-23` |
| Frontend sorts/colors violations on `critical/high/medium/low`; unknown → lowest | `frontend/lib/highlightMarkup.ts`, `types.ts:48` |
| Frontend highlighting requires `violation.current_text` verbatim in the document | `frontend/lib/highlightMarkup.ts:21-40` |
| `ScoreHero` renders any `scores` Record keys as per-category chips | `frontend/components/report/ScoreHero.tsx:22-24` |
| Analysis output model is `ComplianceAnalysisResult` (requires `current_text`) | `compliance_schemas.py:29` |

---

## 3. Decisions (from stakeholder Q&A)

1. **Architecture:** Integrate the precedent corpus as a **new RAG index**
   (`rag_compliance_examples`) inside the existing `services/rag/` abstraction.
   Reuse embedders, `PgVectorStore`, migration style, ports/factory. No
   parallel stack.
2. **Vocabulary:** Use the brief's **new vocabulary** — severity
   `critical / moderate / informational`, and reviewer-derived violation
   categories (`terminology issue`, `legal language`, `missing reference`,
   `disclaimer issue`, `other`). Accepted consequence: frontend severity
   sorting/color-coding won't recognize `moderate`/`informational`.
3. **Retrieval flow:** **Replace** rule retrieval with precedent retrieval in
   the analysis path (no rule fallback).
4. **Scoring weights:** **Extend `SEVERITY_WEIGHTS` additively** with
   `moderate` and `informational` keys. Existing keys and all scoring logic
   stay byte-identical.
5. **Empty / missing precedents:** **No rule fallback.** A chunk with no
   retrieved precedents yields no violations for that chunk. **Operational
   guard:** when the KB is entirely empty, set `metadata.degraded =
   "knowledge_base_empty"` and log a prominent warning (observable, not
   silent).
6. **Verification:** Full regression + ingestion smoke test (Docker up, alembic
   upgrade, all existing endpoints 200, ingest sample JSON, one end-to-end
   precedent analysis, confirm frontend contract shapes unchanged).
7. **Corpus shape:** Each JSON file is a **single review pass** (no multi-round
   chains). Therefore NO trajectory columns (`iteration_number`, `review_round`,
   `resolved_status`, `previous_comment_id`) are added — they'd be unfillable
   NULLs (YAGNI). `document_id` is stored as the only grouping hook; ingestion
   reports duplicate-`document_id` counts as a data-validation signal.
8. **Eval in Phase 1:** Include a **minimal, leakage-safe replay/regression
   harness** (see §17.5). Full metric dashboard deferred to Phase 2.
9. **Phasing:** This plan = **Phase 1** (runtime precedent integration + minimal
   eval). The offline **Compliance Intelligence Builder** (semantic taxonomy /
   clustering, structured `rewrite_patterns` mining, reviewer profiling, embedding
   retraining, full benchmark suite) is **Phase 2** — its own spec/plan, built
   after Phase 1 lands. See §20.

---

## 4. Architecture Overview

```
JSON corpus ──► KnowledgeBaseIngestionService ──► compliance_examples_indexer
                  (parse / chunk / align / classify / embed)        │
                                                                    ▼
                                                  rag_compliance_examples (pgvector)
                                                                    ▲
                                                                    │ hybrid_search
Document ─► preprocess_node ─► dispatch_node ─────────► PrecedentRetriever
                                   │ attaches retrieved_examples to state
                                   ▼
                              analysis_node (per-chunk, few-shot precedent prompt,
                                   temp 0, validate+retry) ─► ComplianceAnalysisResult
                                   ▼
                              scoring_node (UNCHANGED) ─► refinement_node/HITL (UNCHANGED)
```

The 5-node graph, its edges, checkpointing, and HITL interrupt are unchanged.
Only the *internals* of `dispatch_node` and `analysis_node` change, plus new
data/ingestion/retrieval components.

---

## 5. Data Model & Migration

**New file:** `backend/alembic/versions/0004_compliance_examples.py`
(revision `"0004"`, down_revision `"0003"`), authored in the style of `0002`:

- `EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))` at module level.
- `CREATE EXTENSION IF NOT EXISTS vector` (idempotent; already present but safe).
- Raw-SQL `CREATE TABLE rag_compliance_examples`:

```
id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
document_id TEXT NOT NULL,
title TEXT,
task TEXT,
section_label TEXT,
chunk_text TEXT NOT NULL,
anchor_text TEXT,
reviewer_name TEXT,
comment_text TEXT NOT NULL,
final_text_chunk TEXT,
violation_category TEXT,
severity TEXT,
source_file TEXT NOT NULL,
embed_text TEXT NOT NULL,          -- "Document chunk: …\nCompliance comment: …"
embedding VECTOR({EMBED_DIM}) NOT NULL,
search_tsv TSVECTOR,
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

- Indexes: IVFFlat `(embedding vector_cosine_ops) WITH (lists=100)`, GIN on
  `search_tsv`, BTREE on `reviewer_name`, `violation_category`, `severity`,
  `document_id`, `source_file`.
- tsvector trigger mirroring the `0002` pattern (weight chunk_text A,
  comment_text B).
- Full `downgrade()` dropping trigger/function/indexes/table.

> Note: brief specified an HNSW index + hardcoded `vector(1536)`. We instead
> use **IVFFlat + env-driven dim** to stay consistent with the existing three
> indexes and preserve provider-swap (Cohere=1024) without migration edits.

---

## 6. Vector Store Changes

**`backend/app/services/rag/ports.py`**: add `"rag_compliance_examples"` to the
`IndexName` Literal.

**`backend/app/services/rag/stores/pgvector_store.py`**:
- Add the index to the upsert SQL dispatch (its own `INSERT … ON CONFLICT (id)
  DO UPDATE` branch with the new columns + `CAST(:embedding AS VECTOR)`).
- Add to `_FILTER_WHITELIST`: `{reviewer_name, violation_category, severity,
  document_id}`.
- Vector/keyword leg SQL is index-name parameterized already; confirm it covers
  the new table (tsvector column name `search_tsv` matches).

**Pinecone / Azure stores:** pgvector is the configured default
(`settings.rag_vector_backend = "pgvector"`). For v1 the new index is supported
on pgvector only; the other stores raise `RAGDegraded` for this index rather
than silently mis-indexing. (Documented limitation.)

---

## 7. Ingestion

**New:** `backend/app/services/knowledge_base_ingestion.py` —
`KnowledgeBaseIngestionService` and
`backend/app/services/rag/indexers/compliance_examples_indexer.py`.

Pipeline per file:
1. **Parse JSON** (confirmed schema). Validate required fields; descriptive
   error if missing.
2. **Chunk** `input.draft_text` with `RecursiveCharacterTextSplitter`
   (`KB_CHUNK_SIZE=500`, `KB_CHUNK_OVERLAP=50`). Chunk `output.final_text` in
   parallel for `final_text_chunk` pairing.
3. **Parse comments** from `input.compliance_comments` line-by-line via regex:
   reviewer (inside `[...]`), comment (between `]:` and `(Context:`), anchor
   (inside `(Context: …)`). Malformed lines → `logs/parse_errors.log` with
   filename + line number. Never silently drop.
4. **Align** each comment to its best chunk: exact `anchor_text` substring
   match first; else `rapidfuzz.fuzz.partial_ratio` on first 100 chars,
   threshold `KB_MIN_FUZZY_SCORE=60`. Unmatched comments counted + logged.
5. **Classify (no LLM):**
   - `violation_category` from comment keywords:
     `terminology|rephrase|word|rename|phrase` → `terminology issue`;
     `legal|legally|irdai|regulatory|regulation|compliance` → `legal language`;
     `link|url|reference|refer|source|cite` → `missing reference`;
     `disclaimer|disclosure|disclaim` → `disclaimer issue`; else `other`.
   - `severity` from `reviewer_name`: contains `Legal`/`Compliance` → `critical`;
     contains `Marketing` → `moderate`; else `informational`.
6. **Embed** `f"Document chunk: {chunk_text}\nCompliance comment: {comment_text}"`
   via `get_embedder()` (NOT LangChain embeddings — reuse existing provider).
7. **Batch upsert** (`KB_BATCH_SIZE=100`) via the indexer → store.
   Idempotent: skip when `(source_file, chunk_text, comment_text)` already
   present.

**Stats** (`get_stats()`): total count, counts by violation_category / severity
/ reviewer_name, distinct source_files, most-recent `created_at`.

**CLI:** `backend/scripts/ingest_knowledge_base.py` mirroring `seed_rules.py` /
`ingest_guidelines.py` (sys.path bootstrap, `if __name__ == "__main__"`):
`--folder`, `--preview` (parse one file, print chunk↔comment pairings, wait for
Enter), `--limit N`, tqdm file-level progress, final summary (processed /
failed+errors / inserted / skipped-dupes / unmatched-comments / elapsed).

**New dependencies:** `rapidfuzz`, `langchain-text-splitters` (verify vs.
existing `langchain>=0.3.25`; add to `requirements.txt`).

---

## 8. Retrieval & dispatch_node

**New:** `backend/app/services/rag/retrievers/precedent_retriever.py` —
`PrecedentRetriever.retrieve_per_chunk(chunks, top_k)` → `{chunk_id:
[precedent_dict, …]}` via `store.hybrid_search("rag_compliance_examples", …)`.
`top_k` from new `PGVECTOR_TOP_K` (default 5). Singleton accessor
`get_precedent_retriever()` matching existing retriever modules.

**`dispatch_node` changes** (replace path, no fallback):
- Call `PrecedentRetriever.retrieve_per_chunk(chunks, top_k)`.
- Attach to state under new key **`retrieved_examples`** (`{chunk_id:
  [precedent, …]}`).
- Emit a synthetic `active_agents` / categories list sufficient to keep the
  graph well-formed and the analysis loop driven (see §9 — loop is now
  per-chunk, so categories are no longer the driver).
- KB-empty guard: if `get_stats()`/retrieval shows zero precedents across all
  chunks, set `metadata.degraded = "knowledge_base_empty"` + `logger.warning`.
- The legacy rule-retrieval code remains in the file (not deleted) but is no
  longer on the primary analysis path. Rule CRUD endpoints + tables remain fully
  operational.

**State change:** add optional `retrieved_examples:
Dict[str, List[Dict[str, Any]]]` to `ComplianceState` (`state.py`). Existing
keys unchanged → downstream-compatible.

---

## 9. analysis_node & Prompting

**`analysis_node` restructured to per-chunk** (one grading call per chunk using
that chunk's retrieved precedents) instead of `chunk × category`:
- For each chunk: gather `retrieved_examples[chunk_id]`; if empty → skip
  (no violations), per Decision 5.
- Build the prompt via a **new** `create_precedent_prompts(content, precedents)`
  method on `ContextEngineeringService` (sibling to `create_compliance_prompts`).
- Call the LLM with `output_model=ComplianceAnalysisResult` (UNCHANGED output
  contract → scoring/frontend/DB persistence unaffected) at **temperature 0**.
- Run the existing **critic** pass? The critic critiques against cited rules;
  with precedents there is no `rule_id`. v1: bypass the rule-based critic for
  the precedent path (it would drop all findings for lacking rule_ids). Noted as
  a follow-up if a precedent-aware critic is wanted.
- Run `validate_agent_output`; retry once on failure; on repeat failure log to
  `logs/grade_errors.log` and continue.

**Prompt (per brief), built by `create_precedent_prompts`:**
- System prompt: senior Bajaj Allianz reviewer, examples are ground truth,
  imitate tone/severity/phrasing, never introduce categories/terminology not in
  examples, output only valid JSON.
- Few-shot block: for each precedent — Reviewer, Original text (chunk_text),
  Compliance comment, Violation type (violation_category), Severity.
- Then "NEW DOCUMENT SECTION:" + the chunk text.
- **Output still requires `current_text`** (verbatim problematic phrase from the
  *new* document section) so frontend highlighting keeps working, plus
  `suggested_fix` (informed by precedent `final_text_chunk` patterns),
  `category` (new vocab), `severity` (new vocab), `description`, `confidence`.

**Temperature:** verify `llm_service.generate_structured_response` accepts a
temperature override; if not, add an optional `temperature` param (default
preserves current behavior) and pass `0` from the precedent agent. This is the
only `llm_service` change and is additive.

---

## 10. Validation Layer

**New:** `validate_agent_output(output: dict) -> tuple[bool, list[str]]` in a
validators util module. Checks: parseable dict; `violation_found` boolean (if
present); `category` non-empty str; `severity` ∈ {critical, moderate,
informational}; `description` ≥ 10 chars; `score_impact`/`confidence` ∈ [0,1].
Returns (ok, all_errors). The precedent agent runs it after the LLM call,
retries once with a corrective suffix, then logs + continues. Never crashes the
graph.

---

## 11. Scoring weight extension (Decision 4)

`backend/app/services/agents/compliance/scoring.py`: extend the dict only:

```python
SEVERITY_WEIGHTS = {
    "critical": 20, "high": 10, "medium": 5, "low": 2,
    "moderate": 8, "informational": 2,   # added — new precedent vocab
}
```

No logic change. `_get_status` still triggers "failed" on `critical` (present
in new vocab). All existing severities behave identically.

---

## 12. API Endpoints

**New router** `backend/app/api/routes/knowledge_base.py` —
`APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])`, registered in
`main.py` via `app.include_router(knowledge_base.router)`:
- `POST /knowledge-base/ingest` — body `{folder_path, preview?, limit?}` →
  ingestion summary.
- `GET /knowledge-base/stats` — → `get_stats()` output.

Follows existing conventions: `async def`, `db: Session = Depends(get_db)`,
dict responses with stringified IDs, `HTTPException` for errors.

---

## 12.5 Knowledge-Base Visualization (Phase 1)

User-requested: *see* how precedents/rules/docs sit in vector space.

**Backend** — add to the knowledge-base router:
- `GET /knowledge-base/projection?method=umap&refresh=false` → projects the
  embeddings of `rag_compliance_examples` + `rag_rules` + `rag_source_docs` into
  2-D.
- **Single UMAP fit** over ALL selected points stacked into one matrix (separate
  per-index fits produce non-comparable spaces). PCA fallback (sklearn) if
  `umap-learn` import fails or n<4.
- **Caching:** module-level cache keyed on `f"{method}:{n_examples}:{n_rules}:
  {n_source_docs}:{cap}"` (signature from `SELECT count(*)` per table). Hit →
  return cached; `refresh=true` forces recompute. UMAP is too slow to run per
  page load.
- **Point cap:** `VIZ_POINTS_PER_INDEX` (default 2000) via `ORDER BY random()
  LIMIT` per table, to bound payload + compute.
- Embeddings read via raw SQL (`SELECT id, embedding::text, …`); parse the
  pgvector literal `"[..]"` into floats (store registers no pgvector type).
- Response: `{ method, computed_at, counts, points: [{id, index, x, y,
  category, severity, reviewer_name, label, snippet}] }`.
- New service module `backend/app/services/vector_projection.py`.

**Frontend** (additive — overrides the brief's "no frontend" rule per explicit
user request; no existing page's behavior is changed):
- New route `frontend/app/(workspace)/knowledge-base/page.tsx`.
- New component `frontend/components/knowledge-base/VectorSpaceScatter.tsx` using
  **recharts `ScatterChart`** (already a dependency — no new npm package),
  mirroring `CategoryRadar` card/theme conventions. Points colored by index;
  severity/category filter chips; hover tooltip shows snippet + reviewer +
  severity + category.
- New `getKnowledgeBaseProjection()` in `frontend/lib/api.ts` (+ `ProjectionPoint`
  / `ProjectionResponse` types in `frontend/lib/types.ts`).
- One new entry in `frontend/components/workspace/Sidebar.tsx`.

---

## 13. Config / Env

Add to `config.py` Settings and `backend/.env.example` (no existing key removed):
```
PGVECTOR_TOP_K=5
KB_CHUNK_SIZE=500
KB_CHUNK_OVERLAP=50
KB_BATCH_SIZE=100
KB_MIN_FUZZY_SCORE=60
VIZ_POINTS_PER_INDEX=2000
```
Reuse existing `RAG_EMBEDDING_DIM` / `rag_embedding_*` — do NOT add a parallel
`PGVECTOR_EMBEDDING_*` set (would duplicate config). Docker image change is
**already done** → skipped.

**New backend dependencies (requirements.txt):** `rapidfuzz` (comment↔chunk
alignment), `langchain-text-splitters` (RecursiveCharacterTextSplitter; verify
vs. existing `langchain>=0.3.25`), `umap-learn` + `scikit-learn` (projection;
sklearn also provides the PCA fallback). **No new frontend dependency** —
recharts is already present.

---

## 14. README

Append a "Vector Memory System" section: what the precedent KB is, how it
differs from `rag_rules`, how to ingest (`python -m scripts.ingest_knowledge_base
--folder …`), the two new endpoints, the dispatch/analysis change, and the
expected consistency improvement. No existing section modified.

---

## 15. Out of Scope / Regression Guarantees (UNTOUCHED)

Graph node set & edges; `scoring_node` logic (only the weights dict is
extended); `refinement_node` & HITL interrupt; all existing endpoint
signatures; every EXISTING frontend page's behavior (a NEW `/knowledge-base`
page + one Sidebar entry are ADDED — nothing existing is changed); `rules` /
`submissions` / `compliance_checks`
tables; existing migrations `0001`–`0003`; Docker service names/ports/volumes;
existing RAG indexes & retrievers (rules/chunks/source_docs remain functional);
rule CRUD + generation endpoints.

---

## 16. Accepted Degradations (explicit)

- Frontend severity sort/color won't recognize `moderate`/`informational`
  (they sort lowest, get default styling). Per Decision 2.
- Per-category score chips will show new category labels
  (e.g. "terminology issue"). Renders fine; just new labels.
- Empty KB → no violations + `metadata.degraded="knowledge_base_empty"` (logged,
  observable). Per Decision 5.
- Rule-based critic is bypassed on the precedent path in v1.

---

## 17. Verification Plan (Decision 6)

1. `docker compose up` (pgvector pg15) + `alembic upgrade head` reaches `0004`.
2. Hit every existing endpoint (submissions, compliance analyze/sync/results,
   rules CRUD, dashboard, similar, health, rag health) → expect 200 + unchanged
   shapes.
3. Ingest a small sample (`--limit 10 --preview`) of JSON files; confirm
   `GET /knowledge-base/stats` counts.
4. Run one submission end-to-end through precedent analysis; confirm violations
   carry `current_text`, new-vocab `category`/`severity`, score computed.
5. Confirm frontend types still satisfied (violation/score/results shapes).
6. Empty-KB run shows the `knowledge_base_empty` degraded signal.

---

## 17.5 Minimal Replay / Regression Eval (Phase 1)

Measures the migration's stated goal — reproducing reviewer behavior — on
held-out data. **New script:** `backend/scripts/eval_precedent_replay.py`.

- **Leakage-safe split:** deterministically partition the JSON corpus by a hash
  of `source_file` into `train` / `eval` (default 90/10 via `--eval-frac`).
  Ingest **only `train`** into a (fresh) KB; evaluate on `eval`. No eval
  document's own precedents can be retrieved → no answer leakage. Defense in
  depth: retrieval also excludes the same `document_id` (whitelisted filter).
- **Per eval doc:** run the precedent analysis path on `input.draft_text`;
  collect generated violations.
- **Ground truth:** parse `input.compliance_comments` (same parser as ingestion)
  → real reviewer comments + their severities.
- **Metrics (printed + JSON to `logs/eval_replay.json`):**
  - violation-presence **precision / recall** (a chunk/anchor the reviewer
    commented on vs. chunks we flagged — alignment by anchor/chunk overlap),
  - **missed-criticals** count (real critical comments with no generated match),
  - **comment cosine-similarity** (mean cosine between generated `description`
    embeddings and matched real `comment_text` embeddings, via `get_embedder()`),
  - counts: docs evaluated, generated vs. real violation totals.
- Pure-Python metric helpers are unit-tested; the live run is a CLI invocation
  (needs DB + LLM), reported in the smoke test.

---

## 18. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Empty KB silently passes everything | `degraded` metadata + prominent log (Decision 5) |
| Per-chunk loop change breaks parallelism/tracing | Preserve `asyncio.gather`, AgentExecution tracing per chunk |
| New severity miscalibration | Extended `SEVERITY_WEIGHTS` (Decision 4) |
| Frontend can't sort new severities | Accepted (Decision 2); documented |
| Embedding dim mismatch on provider swap | Reuse env-driven `RAG_EMBEDDING_DIM`; same as existing indexes |
| rapidfuzz/text-splitter missing in image | Add to requirements; rebuild backend image |
| Critic drops precedent findings (no rule_id) | Bypass rule-critic on precedent path in v1 |

---

## 19. Execution Order (for the implementation plan)

1. Migration `0004` + `IndexName` + `pgvector_store` wiring.
2. `requirements.txt` (rapidfuzz, langchain-text-splitters) + config/env keys.
3. `compliance_examples_indexer` + `KnowledgeBaseIngestionService`.
4. CLI `ingest_knowledge_base.py`.
5. API router + register.
6. `PrecedentRetriever` + `ComplianceState.retrieved_examples`.
7. `dispatch_node` → precedent retrieval (replace, KB-empty guard).
8. `create_precedent_prompts` + analysis_node per-chunk restructure + temp 0.
9. `validate_agent_output` + retry/logging.
10. `SEVERITY_WEIGHTS` extension.
11. Eval replay harness (`eval_precedent_replay.py`) + metric helpers.
12. Vector projection service + `GET /knowledge-base/projection` (umap-learn,
    single-fit, cached) + `umap-learn`/`scikit-learn` deps + `VIZ_POINTS_PER_INDEX`.
13. Frontend `/knowledge-base` page + `VectorSpaceScatter` (recharts) + `lib/api.ts`
    fn + `lib/types.ts` types + one Sidebar entry.
14. README section (incl. visualization).
15. Full regression + ingestion smoke test (incl. one eval replay run + the
    projection endpoint returning points + the new page rendering).

---

## 20. Phase 2 Roadmap (OUT OF SCOPE for this plan)

Captured here so Phase 1 stays focused; each becomes its own spec/plan.

- **Offline Compliance Intelligence Builder:** scheduled jobs over the ingested
  corpus — semantic clustering of comments, taxonomy generation, embedding
  retraining/fine-tuning, reviewer profiling.
- **Structured `rewrite_patterns`:** mine `(violation → before → after)` triples
  from the stored `chunk_text` / `final_text_chunk` / `comment_text` (raw signal
  already persisted in Phase 1) → auto-suggestion + RLHF signals.
- **Semantic taxonomy** to replace the v1 keyword classifier — runs as an offline
  `UPDATE` over `violation_category` (no re-ingestion; raw `comment_text` retained).
- **Full benchmark suite / dashboard:** drift tracking, per-reviewer fidelity,
  longitudinal precision/recall, CI regression gates.
- **Trajectory modeling:** only if a future corpus provides multi-round data keyed
  by `document_id` (current corpus is single-pass — Decision 7).
