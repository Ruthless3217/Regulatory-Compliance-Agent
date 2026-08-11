---
type: Change Log
title: Bundle Change Log
description: Chronological history of changes to this OKF knowledge bundle.
resource: ./log.md
tags: [okf, log, history]
timestamp: 2026-07-03T12:00:00Z
---

# Change Log

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
