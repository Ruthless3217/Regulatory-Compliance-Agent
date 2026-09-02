---
type: Change Log
title: Bundle Change Log
description: Chronological history of changes to this OKF knowledge bundle.
resource: ./log.md
tags: [okf, log, history]
timestamp: 2026-07-03T12:00:00Z
---

# Change Log

## 2026-09-02 — Audit-driven remediation (9 domains, 10 parallel implementers)

A read-only audit of every feature domain (bugs, dead code, redundancy, performance), each finding adversarially verified,
then fixed by parallel implementers with disjoint file ownership. Suite: **983 passed** (was 965); `tsc` clean; vitest 62
(was 47); `next build` clean.

- **Security / visibility** — `ip_allowed` now receives the `User` row, so `cidr`/`list` IP-binding modes are enforced (they
  fell through to allow); `/health/rag` + `/debug/rag/search` require `knowledgebase:view`; admin-console passwords enforce
  `MIN_PASSWORD_LEN`; dashboard `recent_submissions` respects `visible_submission_filter`; every violation/check/run/comment
  scoped handler in `compliance.py`/`submissions.py` (10 routes incl. `GET /check/{id}`) now calls `get_visible_submission`
  (404-not-403), guarded by `test_visibility_route_coverage.py`; `assign()`/`reassign()` reject unknown/inactive assignees
  with a 4xx instead of an IntegrityError.
- **Bugs** — `brochure_parser.flush()` no longer drops body text before the first heading; `azure_search_store._client()`
  raises `RAGDegraded` on an unmapped index; `positioned_words(cap)` matches `render_pages`' page cap so capped renders never
  reference unrendered pages; `middleware.ts` matcher excludes `/api` (expired cookie no longer 307s API calls to the login
  page); rules-generation temp upload is removed after extraction; duplicate `_redis_allow` in `rate_limit.py` removed.
- **Editor** — a genuine select-all-delete now persists (the empty-emission guard moved into `LexicalDocument`'s per-mount
  `seededRef`); Apply-fix refuses an ambiguous multi-occurrence splice; version history is reachable on the rich-editor path
  and restores re-seed the Lexical state (`_serialize_revision` now emits `lexical_state`/`lexical_html`); the Report tab's
  button is honestly labelled "Copy fix".
- **Performance** — `PrecedentRetriever.retrieve_per_chunk` fans out per chunk with `asyncio.gather` (was sequential);
  `comparisons.py` runs extraction/diff/search under `run_in_threadpool`; the compare viewer derives its change list once
  in `ViewerProvider` instead of three times per render.
- **UX** — corpus-layer operations reachable below `xl`; product-scope labels via `productScopeLabel` (were mangled by
  `categoryLabel`); dashboard volume chart gets a legend and a distinct Reviewer-added colour; super-admin Add User works;
  super-admin pages use `formatDate`; the New-analysis scope chips are static badges (they never affected the request);
  truncated-pages banner restored in the compare viewer.
- **Deleted (zero callers, grep-verified)** — `agents/{agent_factory,base_agent,standard_agent}.py`, `redaction.py`,
  `entity_registry.py` (+ `data/entities/balic.json`, its test), `LLMService.stream_response`/`_record_budget_tokens`/
  `_get_fallback_response`, orchestrator `get_state`/`resume_workflow`, `comparison_service.extract_paragraphs` family +
  `normalize_for_match`, `llm_budget.is_over_budget`/`reset_global_budget`, `rag.factory.reset_singletons`,
  `pgvector_store.reset_embedding_check_cache`, `vector_projection.project_2d`/`_count`, `source_docs_retriever.semantic`,
  `preprocessing_service._cap2`, four unused `schemas/submission.py` classes, three ad-hoc `backend/scripts/_*.py` probes,
  `gunicorn`/`markdown`/`pandas` from requirements; frontend `components/compare/*` (3 files), `Masthead`, `KPICards`,
  `SeverityHeatmap`, `ui/skeleton`, `ui/scroll-area`, `deriveChanges`/`ChangeItem`, nine unused `lib/api.ts` functions,
  `@radix-ui/react-scroll-area`.
- `required_disclosures` is now persisted in run metadata (`_RUN_METADATA_KEYS`), making the disclosure node's audit-trail
  docstring true. Plan + per-finding evidence: `docs/superpowers/plans/2026-09-02-feature-audit-remediation.md`.

## 2026-08-11 — Determinism, chunk reuse, product segregation, feedback layer, anchoring, inspector

Eight coordinated changes (migrations `0035`–`0037`; suite 840 tests green):

- **Chunk-level analysis reuse** — re-analysis finally chunks `current_content` (previously it silently re-graded
  `original_content` forever); `content_chunks.content_hash` + `context_key` (migration `0035`), unchanged chunks skip all
  LLM calls and carry their violations forward (re-parented, reviewer verdicts preserved); `PROMPT_VERSION` constant in
  `analysis_cache.py` is a manual bump-on-prompt-change contract. `violations.chunk_id` is now written.
- **Determinism** — LangGraph thread id is per-run (was per-submission: the `operator.add` reducer accumulated violations
  across runs, empirically 3→6→9); Redis checkpoints get a 24h TTL; content-derived prompt fences replace `uuid4`;
  `LLM_SEED` supported; stable `, id` tiebreaks on both retrieval legs; `get_active_rules` ordered; partially-failed
  disclosure sweeps now fail closed (`disclosure_recall_degraded` blocks persistence).
- **Compare Draft fixed** — `draft-diff` baseline now derives from the Lexical import lineage (`import_text`), not the
  analyser extraction, so extractor disagreement no longer renders as phantom reviewer edits. DOCX DrawingML text boxes
  are now imported (deduped against mammoth's own VML handling).
- **Reviewer feedback layer** — `POST /violations/{id}/actions` verdicts (`correct`/`not_violation`) now upsert a
  precedent (keyed uuid5 on violation id) into an auto-created "Reviewer feedback" corpus layer; disabling the layer
  removes the whole contribution from retrieval (`_LAYER_GUARD`). `not_violation` precedents render in a separate
  REVIEWER REJECTIONS prompt section and citations resolving to them are dropped.
- **Product segregation (soft)** — `product_line` on `rag_rules`/`rag_chunks`/`rag_source_docs`/`rag_product_docs`
  (migration `0036`, join backfills; `backfill_product_metadata.py` for file-derived rows), filtered in BOTH legs via the
  shared clause (`IN (…) OR IS NULL`); indexers stamp product going forward; `applicability.scope_filter_values` is the
  single vocabulary source (covers the `_PRODUCT_HINTS` casing variants). `exclude_document_id` leakage guard now wired.
- **Guideline ingestion un-broken** — `ingest_guidelines.py` had been a silent no-op since `e6304b4` (missing mandatory
  `product_line`); now derives product from the filename prefix, fails loudly, and ULIP guidelines are IRDAI, not SEBI.
- **Stable violation anchoring** — analysis chunks are built over the same block list the Lexical editor renders
  (block id = `sha1(normalize(text))[:12]` + ordinal, byte-identical frontend `sectionMap.ts` / backend `lexical_anchor.py`);
  `anchor_node_key`/offsets/`anchor_fingerprint`/`section_title` are now written (previously provisioned by `0034`, never
  populated). Editor: bubbles in Edit+Split with clustering and leader lines; apply-fix routes through a discrete Lexical
  update (previously it desynced `current_content` from `lexical_state`, shipping un-fixed wording in approved exports).
- **Retrieval inspector (full tracing)** — per-candidate rows in `retrieval_candidates` (migration `0037`): both legs'
  scores/ranks, RRF rank, applicability verdict+reason, `final_status` + `deciding_stage` (incl. the previously
  unrecorded 8-rule cap); contextvar trace captured inside `hybrid_search`; new `/admin/retrieval/runs/{id}/candidates`
  + `/chunks` endpoints and UI drill-in. Pre-`0037` runs answer with an explicit no-data contract.

Known-stale notes: `RETRIEVAL_RCA.md` §C2/C7 describe untagged-as-global; the shipped applicability judge is deliberately
fail-closed (untagged ⇒ rejected `scope_metadata_missing`). ~81 active rules with NULL `product_line` are excluded from
analysis until manually scoped (rules-page banner now says so honestly).

## 2026-07-12 — Compare viewer clone

- Full-screen Compare viewer (Draftable-style) in a new `(viewer)` route group; `/compare/[id]` opens in a new tab.
  New UI module `frontend/components/compare-viewer/*`.
- Pixel "Document view" backend now wired (**PDF-only**): `render_orchestrator.run_render` (BackgroundTask),
  `GET /comparisons/{id}/pages/{side}/{n}`, `_serialize` returns render fields. `pdf_render_service.to_pdf` is
  PDF-passthrough; `gotenberg_client.py` kept as the DOCX re-enable seam.
- Move detection (`moved` blocks + changes), notes & tags (`comparison_annotations`, migration `0020`), positioned
  search (`…/search`), export pack (`…/export/{kind}`), and Adjust-Comparison re-run (`…/rerun`).
- Updated pages: services (index, comparison-service), data-model (document-comparisons), frontend (routing), api (index).

## 2026-07-03 — v0.1 initial bundle

- Created the OKF bundle for the Regulatory Compliance Agent.
- Captured concepts across seven sections: architecture, services, rag, data-model, api, frontend, config.
- Sourced from the repository code and `docs/ARCHITECTURE.md` as of commit `9145ce8` (branch `main`).
- Notes recorded where the `README.md` is stale relative to the deployed system (6-node pipeline on Azure gpt-5.4, not the
  5-node/Gemini description in the README).

> Maintenance convention: when a concept's underlying code changes, update the concept's `timestamp` and add a dated line here.
