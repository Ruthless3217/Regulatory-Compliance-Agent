# Interactive Compliance Review Workspace — Architecture & Task Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement task-by-task.

**Goal:** Fix the stuck-"Analysing" bug, then build re-analysis, a real document viewer, a reviewer-action taxonomy (Correct/Not-a-violation/Dismiss), document editing+export, a Model Learning tab, KB/reranking diagnostics, and rule-pipeline traceability fixes — on top of the existing FastAPI+LangGraph+Next.js compliance-review system, reusing what already exists (analysis_runs, rule_feedback, Compare-viewer's PDF renderer) instead of rebuilding it.

**Method:** 9 parallel investigate/design agents (one per goal area) + 1 consolidation pass, run via Workflow on 2026-07-30. Full per-area reports are in the workflow journal (`wf_646f0af9-6aa`); this doc is the consolidated, de-conflicted result.

## Current-state architecture map

One coherent pipeline underlies all nine areas: upload → `preprocess_node` (chunk + `product_resolver.resolve_products`, `backend/app/services/agents/graph/nodes.py:679-762`) → `dispatch_node` (loads active rules, product-aware retrieval scoping via `backend/app/services/rag/applicability.py`, per-chunk rules/precedent retrievers, `nodes.py:813-1039`) → `analysis_node` (4-tier grading: precedent/rule/novel/product_fact, `nodes.py:1042-1320`) → `disclosure_node` (deterministic disclaimer checker, `nodes.py:1342-1404`) → `scoring_node` → `ComplianceEngine.analyze_submission` (`backend/app/services/agents/compliance/engine.py:94-261`, row-locks the submission, runs the graph, `persist_results` writes `ComplianceCheck`+`Violation` rows and flips `submissions.status` atomically). Fired fire-and-forget from `POST /compliance/analyze/{submission_id}` (`backend/app/api/routes/compliance.py:37-65`) via `background_tasks.add_task`; the frontend learns the outcome through a hand-rolled `fetch()+ReadableStream` SSE (`frontend/lib/sse.ts`, `useSSEStream`) with zero reconnect logic, consumed by `frontend/components/review/ReviewTab.tsx` whose `isAnalyzing` flag is derived from a frozen server-fetched `submission.status` prop.

Five load-bearing but incompletely-wired subsystems, each independently found by 2-3 of the nine reports:

1. **`analysis_runs`** (`backend/app/models/analysis_run.py`, `run_tracker.py` `open_run`/`close_run`) already tracks `started_at`/`status`/`finished_at`/`run_number`/`is_rerun`/`duration_ms`/`cost` per execution, live-populated on every analyze call — but only consumed by an admin-only `usage:view` surface (`admin_console.py`). Reviewers, the SSE stream, and the stuck-Analysing bug all ignore this reusable heartbeat/versioning data.
2. **Violation serialization is hand-duplicated** in two places (`compliance.py`'s `_serialize_violation` and `engine.py`'s `get_check_summary`), both silently dropping already-populated columns (`cited_section`, `cited_page`, `cited_regulation_version`, `rule_version`).
3. **`rule_feedback`** (`backend/app/models/rule_feedback.py`, migration 0010) is the only reviewer-feedback store, upserted per (violation, reviewer), mutating `Rule.reliability_alpha/beta` via Beta-Binomial update with no history log, no approval gate, and a binary accept/reject `verdict` column that's `VARCHAR(10)` — too narrow for a richer taxonomy value like `not_violation` (13 chars). The third UI button, **Dismiss**, calls `onDismiss()` which is 100% client-side React state — persists nothing server-side. A live silent-discard bug.
4. **Product/segment scoping** (`applicability.py`, commits e7afd02/18c394b) correctly unions fact-card `structural_flags` across every resolved product for rule/precedent retrieval — but `backend/app/services/disclaimer/triggers.py::derive_product_context` re-derives `is_ulip`/`is_par` by regexing free-text `regulatory_descriptor` instead of reading `structural_flags`, and calls `fact_cards.get(uin)` (single-card) instead of `get_all(uin)` (all UIN-colliding variants) — **confirmed live bug misclassifying Fortune Gain II, Goal Suraksha, Smart Protection Goal today**, found independently by two reports.
5. **Config/settings truthfulness:** `backend/app/config.py` is a clean env-driven pydantic `Settings`, correctly separated from the frontend's `DensityToggle` (pure client cookie, no model coupling to fix) — but `frontend/app/(workspace)/settings/page.tsx` hardcodes a stale model name and rule-count literals while `GET /health/rag` already computes real embedder/backend/model data and is simply never called by the frontend.

Document review today is plain-text `<mark>` highlighting only (`frontend/lib/highlightMarkup.ts` substring-matches against `submission.original_content`) even for PDF-sourced uploads; `violation.location` is a synthetic `"chunk:N"` string with no real page/bbox anchor. The separate Compare feature (`frontend/components/compare-viewer`, `backend/app/services/render_orchestrator.py` + `pdf_render_service.py`) already solved real PDF page rasterization, positioned-word bboxes, and DOCX/PDF export (via a currently-unwired Gotenberg client with zero deployed container) — infra the review workspace should reuse, not reinvent. Submissions have no content-versioning (`original_content` is the only text field); `ViolationCard`'s "Apply fix" only copies to clipboard; no `document_comments` concept exists. Dashboard (`backend/app/api/routes/dashboard.py`) is purely operational, zero overlap with feedback/calibration/reliability history — which has no source of truth today (`Rule.reliability_alpha/beta` overwritten in place, no audit trail, no prompt/model-version column anywhere).

## P0 bug: root cause and fix location

**Root cause — three independent, compounding defects** on the same lifecycle (upload → fire-and-forget analyze → `layout.tsx` server-fetches submission as a frozen prop → `ReviewTab.tsx` derives `isAnalyzing` from that prop → hand-rolled SSE with zero reconnect):

- **(A)** `ReviewTab.tsx`'s SSE `error` handler (unlike its `done` handler, ~lines 49-51) only does `toast.error(...)` — never calls `router.refresh()` or flips local state. Any terminal error event (analyzer exception → `failed`, or `needs_review`) leaves the progress bar showing forever; only a hard reload recovers.
- **(B)** `frontend/lib/sse.ts`'s `useSSEStream` has zero reconnect and zero fallback poll; effect depends only on `[path]`, which never changes mid-stream. If the connection is severed before `done`/`error` arrives (nginx `proxy_read_timeout`, network blip, idle timeout on a long LLM-bound run), nothing ever re-derives `submission.status` — no interval, no `visibilitychange`/focus refetch. Switching Review↔Report tab and back doesn't re-run `layout.tsx`'s fetch either.
- **(C)** `backend/app/services/agents/compliance/engine.py`: if the process running `_runner()`/`analyze_submission` is hard-killed between the row claim (`engine.py:134`, `status='analyzing'`, committed) and completion, the `except Exception` block (`engine.py:247`) never runs (a process kill doesn't unwind Python exceptions) — leaving `submissions.status='analyzing'` permanently in Postgres. No heartbeat/staleness check exists anywhere. Both recovery paths (`POST /analyze/{id}` and the engine's own guard) silently no-op whenever `status=='analyzing'`, so **the Re-run button cannot un-stick a wedged submission.**
- **Minor adjacent bug, same path:** `frontend/components/ui/status-pill.tsx:49`'s `statusTone()` checks `'waiting_for_review'` but the engine sets `'needs_review'` (`engine.py:191`) — never matches, renders neutral instead of warning tone.

**Fix location:**
- `frontend/components/review/ReviewTab.tsx` — SSE `error` handler must call `router.refresh()` + set a local terminal flag exactly like `done`; add a window `focus`/`visibilitychange` listener that calls `router.refresh()` once while `isAnalyzing` is true.
- `frontend/lib/sse.ts` — wiring for the fallback listener.
- `frontend/components/ui/status-pill.tsx:49` — `'waiting_for_review'` → `'needs_review'`.
- `backend/app/api/routes/compliance.py:54-55` and `backend/app/services/agents/compliance/engine.py:124-130` — both need the same new staleness/orphan check added **alongside** (not replacing) the existing guard: look up the latest `AnalysisRun`; if `status=='running'` with `started_at` older than a new `settings.stale_analysis_run_minutes` (default ~15, `backend/app/config.py`) and `finished_at IS NULL`, treat as orphaned and allow reclaim.

**Risks:** the staleness override must sit alongside the `with_for_update` lock, not replace it (no two truly-concurrent runs). If SSE is ever migrated to native `EventSource`, its auto-reconnect after a `done`-triggered close would silently re-trigger a full re-analysis unless reconnect logic checks terminal status first — `_analyze_and_stream` doesn't currently guard against this. Pick the staleness threshold conservatively relative to real p99 analysis duration.

## Migration plan (ordered, collision-resolved)

Three reports independently proposed migration `0023` with different tables — repo's actual latest is `0022_analysis_run_metadata.py` (`down_revision='0021'`). Resolved to one linear sequence:

1. **`0023_reviewer_actions.py`** (P1) — `rule_feedback`: widen `verdict` `String(10)→String(20)` (required: `'not_violation'` is 13 chars); add `reason VARCHAR(40)`, `original_text/suggested_text/final_text TEXT`, `submission_id UUID FK`, `analysis_run_id UUID FK`, `confidence_snapshot FLOAT`, `retrieval_snapshot/product_snapshot JSONB`, `model_version/kb_version VARCHAR(100)`, `routed_queue VARCHAR(30)`, `queue_resolved_at/by/note`. Same migration: `violations` gains `analysis_run_id UUID FK`, `review_status VARCHAR(20)`, `resolved_at TIMESTAMPTZ`. All additive/nullable, zero backfill.
2. **`0024_violation_anchors_and_render_status.py`** (P1) — `violations`: `section_title TEXT`, `anchor_page INTEGER`, `anchor_bbox JSONB` ([x0,y0,x1,y1], matches `pdf_render_service.PositionedWord`). `submissions`: `page_render_status VARCHAR(50)` (processing|completed|failed|skipped).
3. **`0025_submission_current_content.py`** (P1) — `submissions.current_content TEXT`; `original_content` stays immutable (grading/highlighting reference).
4. **`0026_submission_revisions.py`** (P1) — new table `submission_revisions` (id, submission_id FK CASCADE, revision_number, content, source [manual_edit|apply_fix|bulk_apply_fixes|restore], note, applied_violation_ids UUID[], created_by FK, created_at), UNIQUE(submission_id, revision_number).
5. **`0027_document_comments.py`** (P1) — new table `document_comments` (id, submission_id FK CASCADE, anchor_text, page_number, body, resolved BOOLEAN, created_by, created_at, updated_at).
6. **`0028_violation_fix_applied.py`** (P1) — `violations`: `fix_applied BOOLEAN NOT NULL DEFAULT false`, `fix_applied_at TIMESTAMPTZ`. Third independent column-set landing on `violations` (after 0023, 0024) — no name collision, but rebase `violation.py` sequentially through 0023→0024→0028, don't let parallel branches edit the model class simultaneously.
7. **`0029_scoring_policy_and_reliability_history.py`** (P1) — `analysis_runs.scoring_policy_version VARCHAR(32)` (stamped from new `settings.SCORING_POLICY_VERSION`); new append-only table `rule_reliability_events` (id, rule_id FK, rule_feedback_id FK nullable, alpha_before/beta_before/alpha_after/beta_after/theta_before/theta_after FLOAT, created_at), written inside `RuleFeedbackService.apply_feedback`'s existing transaction.

**Deferred (YAGNI, don't build speculatively):** `rag_rules.product_line` column — only if the stale-RAG-index fix proves insufficient. `precedent_cases.product_category` Par/Non-Par tagging — only if product wants precedent-level filtering.

## API changes

- **Extend** `POST /compliance/analyze/{submission_id}` (`compliance.py:37-59`): row-claim moves out of the background task to happen synchronously before the HTTP response returns; response gains `run_id`/`run_number`. Add the orphan/staleness check to both this route's short-circuit and `engine.py:124-130`'s identical guard so they can't disagree.
- **Merge** `GET /compliance/results/{submission_id}` + `GET /compliance/check/{check_id}` onto one new `backend/app/services/violation_serializer.py::serialize_violation()` replacing both existing hand-duplicated serializers. Output gains previously-dropped citation fields plus `section_title`/`anchor_page`/`anchor_bbox` plus `review_status`/`resolved_at` plus left-joined `reviewer_verdict`/`reviewer_comment`.
- **New** `POST /compliance/violations/{violation_id}/actions` — body `{action: 'correct'|'not_violation'|'dismiss', reason?, explanation?, final_text?, severity_override?}`. Delegates weight update to `RuleFeedbackService.apply_feedback` unchanged for correct/not_violation; dismiss skips it. Snapshots original/suggested text, confidence, retrieval, product, model_version, kb_version; computes `routed_queue`; updates `violations.review_status`/`resolved_at`.
- **Keep** `POST /compliance/violations/{id}/feedback` exactly as-is as a back-compat shim (maps accept/reject → correct/not_violation, no reason/explanation).
- **New** `GET /compliance/reviewer-actions/queues?queue=&min_occurrences=` + `POST /compliance/reviewer-actions/{id}/resolve` — gated behind new `feedback:review` scope (admin/super_admin); `feedback:submit` (all roles) still covers `/actions`.
- **New** `GET /compliance/submissions/{submission_id}/runs` + `GET /compliance/runs/{run_id}/diff?against=previous|{run_id}` — reviewer-facing (`submission:read`), distinct from the existing admin-only `GET /super_admin/submissions/{id}/runs` which stays gated behind `usage:view` (keeps cost/duration).
- **New** `GET /submissions/{id}/pages/{n}` — single-sided mirror of the existing `GET /comparisons/{id}/pages/{side}/{n}`.
- **New** `POST/GET /submissions/{id}/revisions`, `GET /submissions/{id}/revisions/{revision_number}` — the one mutation primitive for manual edits/apply-fix/bulk-apply/restore.
- **New** `POST/GET/PATCH/DELETE /submissions/{id}/comments` — mirrors `comparisons.py`'s existing annotation shape.
- **New** `GET /submissions/{id}/export/{kind}` — kinds: `clean.docx`, `clean.pdf`, `annotated.docx`, `annotated.pdf`, `report.docx`, `report.pdf`, `feedback-report.docx`, `feedback-report.pdf`, `bundle.zip`. Requires new `submission_export_service.py` + `gotenberg_client.py` gaining `convert_html_to_pdf()` + a docker-compose Gotenberg service (currently zero container despite client code existing).
- **New** `backend/app/api/routes/model_learning.py`, prefix `/model-learning`: `GET /funnel`, `/precision?by=`, `/calibration`, `/rule-reliability-history?rule_id=`, `/latency`, `/repeated-patterns`. Reuses `dashboard:view`; only `/rule-reliability-history` needs the new table, the rest are computable today with zero schema change.
- **Fix** `PATCH /rules/{id}` content-change branch (`rules.py:182-212`): copy `reliability_alpha/beta` onto the new rule row so learned trust survives an edit; call `_safe_rag_upsert`/`_safe_rag_delete` on the OLD row right after `is_active=False` (today only DELETE does this correctly).
- **Fix** `disclaimer/triggers.py::derive_product_context` (line 65-75, found independently by two reports): `fact_cards.get(uin)` → `fact_cards.get_all(uin)`, OR the `is_ulip`/`is_par` flags across every variant card, matching `applicability.build_scope`'s existing pattern. **Live production bug** (Fortune Gain II, Goal Suraksha, Smart Protection Goal misclassified today).
- **Add** allow-listed `GET /health/models` (config-audit): `llm_provider`, `llm_model`, `critic_llm_model`, `chat_llm_model`, `disclosure_check_enabled`, `product_grounding_enabled` — explicit allow-list, never `settings.dict()`, never any `*_api_key`.

## Frontend changes (grouped by shared-file contention)

- **`ReviewTab.tsx`** (3 reports, land as one coordinated PR): SSE error-handler fix + focus/visibility fallback; historical-run banner + compare-to-latest panel; PDF-pane vs text-mark switch.
- **`ViolationCard.tsx`** (3 reports, one PR): Dismiss/Not-a-violation/Correct real taxonomy with reason pickers + explanation; `reviewer_verdict`-initialized local state; Apply-fix writes the document via revisions instead of clipboard-only.
- **`ViolationsPane.tsx` + `FilterChipBar.tsx`**: Category/Product/Section/Review-status filters; remove dead client-only dismiss `Set`.
- **`SubmissionHeader.tsx` + `SubmissionWorkspaceContext.tsx`** (2 reports, same rerun concern): optimistic "analyzing" flag on rerun click (safe once the analyze route claims synchronously); compact "Run #N of M" picker.
- **`status-pill.tsx`**: single string fix.
- **`highlightMarkup.ts`**: generalize `findSpans` to a generic `{id,text,severity?}[]` shape (violations + comments share one algorithm).
- **`DocumentPane.tsx`**: mouseup/selection handler → freestanding comment popover.
- **New `PdfPagePane.tsx`**: trimmed single-side copy of `compare-viewer/PagePane.tsx`'s box-positioning math (not its zoom/dual-side state machine).
- **New version-history + export popovers**: structurally cloned from `compare-viewer/ExportPopover.tsx`.
- **`lib/api.ts`, `lib/types.ts`**: append-only additions (submitReviewerAction, run-history/diff, revisions/comments/export fetchers; new Violation/Submission fields).
- **New `app/(workspace)/model-learning/page.tsx`** + Sidebar nav entry: parallel-fetch panels, each with a provenance chip ("computed now" vs "blocked — needs X"); a "learning pipeline" status strip (Flags → Awaiting review → Feedback collected → [no gate, warning chip] → Applied to scoring) is the must-have element, not a KPI wall.
- **`settings/page.tsx`**: replace hardcoded model/rule-count literals with live `GET /health/rag` + `GET /health/models` + `GET /rules?...&limit=1` reads. `DensityToggle` is already correctly isolated — nothing to decouple.

## Implementation task list (priority-ordered, with dependencies)

| # | Task | Priority | Depends on | Files |
|---|------|----------|------------|-------|
| 0 | Fix stuck-"Analysing" bug (SSE error-swallow + no reconnect fallback + backend orphan guard) | **P0** | — | ReviewTab.tsx, sse.ts, status-pill.tsx, compliance.py, engine.py, config.py |
| 1 | Make `POST /analyze/{id}`'s row-claim synchronous, return run_id | **P0/P1** | 0 | compliance.py, engine.py, SubmissionHeader.tsx |
| 2 | Migration 0023 (reviewer_actions columns) | P1 | — | 0023 migration, rule_feedback.py, violation.py |
| 3 | Migration 0024 (violation anchors + render status) | P1 | — | 0024 migration, violation.py, submission.py |
| 4 | Migrations 0025-0028 (current_content, revisions, comments, fix_applied) | P1 | — | 4 migration files + 3 model files |
| 5 | Migration 0029 (scoring_policy_version + rule_reliability_events) | P1 | — | 0029 migration, analysis_run.py, rule_reliability_event.py, config.py |
| 6 | Thread `analysis_run_id` through `persist_results` onto each Violation insert | P1 | 2, 1 | engine.py |
| 7 | Reviewer action taxonomy endpoints + RBAC scope | P1 | 2, 6 | compliance.py, rule_feedback_service.py, permissions.py |
| 8 | Consolidated `violation_serializer.py` (merges 2 independent edits to same call sites) | P1 | 2, 3, 6 | violation_serializer.py, compliance.py, engine.py |
| 9 | Reviewer-facing run history + diff endpoints | P1 | 1 | compliance.py |
| 10 | Backend PDF page-render + anchor pass (reuse pdf_render_service, never reinvent) | P1 | 3, 6 | submission_render_service.py, engine.py, submissions.py |
| 11 | Submission content editing + revisions + comments API | P1 | 4 | submissions.py, submission.py schema |
| 12 | Gotenberg infra + submission export service | P1 | 11 | docker-compose*.yml, gotenberg_client.py, pdf_render_service.py, submission_export_service.py, export_common.py, submissions.py |
| 13 | Fix `disclaimer/triggers.py::derive_product_context` UIN-collision bug | P1 | — | triggers.py, test_disclaimer_product_context.py |
| 14 | Rule-pipeline fixes bundle (product_line pass-through, reliability copy-forward, stale-RAG fix, model capture) | P1 | — | rule.py schema, rule_generator_service.py, rules.py, llm_service.py |
| 15 | Model Learning backend endpoints | P1 | 5 | model_learning.py, main.py, rule_feedback_service.py, run_tracker.py, config.py |
| 16 | Frontend: `ViolationCard.tsx` consolidated rewrite | P1 | 7, 8, 11 | ViolationCard.tsx, api.ts, types.ts |
| 17 | Frontend: `ViolationsPane.tsx` filters + remove fake dismiss state | P1 | 8, 16 | ViolationsPane.tsx, FilterChipBar.tsx |
| 18 | Frontend: `ReviewTab.tsx` historical-run banner + PDF/text switch + comment handler | P1 | 1, 9, 10, 11 | ReviewTab.tsx, PdfPagePane.tsx, highlightMarkup.ts, DocumentPane.tsx |
| 19 | Frontend: `SubmissionHeader.tsx` optimistic-status + run picker | P1 | 1, 9 | SubmissionHeader.tsx, SubmissionWorkspaceContext.tsx |
| 20 | Frontend: version-history + export popovers | P1 | 11, 12 | VersionHistoryPopover.tsx, SubmissionExportPopover.tsx, api.ts |
| 21 | Frontend: Model Learning tab page + Sidebar nav | P1 | 15 | model-learning/page.tsx, Sidebar.tsx |
| 22 | Config-audit fixes (model-identity endpoint, settings page live-wiring) | P2 | — | main.py, .env.prod.example, settings/page.tsx |
| 23 | Optional: precedent_category Par/Non-Par ingest heuristic | P2 | — | precedent_ingestion.py |
| 24 | Cleanup: remove dead duplicate `/timeseries` and `/top-rules` routes | P3 | — | dashboard.py |
| 25 | Optional/deferred: `rag_rules.product_line` column | P3 | 14 | 0030 migration, rules_indexer.py, pgvector_store.py, rules_retriever.py |

## Execution status

- [x] Investigate + design (all 9 areas) + consolidate — done via Workflow `wf_646f0af9-6aa`, 2026-07-30.
- [x] Task 0 (P0 bug fix) — done, 2026-07-30. Fixed: `ReviewTab.tsx` SSE `error` handler now calls `router.refresh()` (mirrors `done`); added window focus/`visibilitychange` fallback refresh while `isAnalyzing`; `status-pill.tsx` `'waiting_for_review'`→`'needs_review'` string fix; backend orphan/staleness reclaim via new `run_tracker.find_stale_running_run()`, wired into both `engine.py:124-130`'s guard and `compliance.py`'s `POST /analyze/{id}` short-circuit, gated by new `settings.stale_analysis_run_minutes` (default 15). Verified: `npx tsc --noEmit` clean, full backend suite 132/132 passing incl. 5 new unit tests in `tests/test_run_tracker_staleness.py`.
- [ ] Task 1 (synchronous row-claim + return run_id) — not yet done; the P0 fix above did not touch the fire-and-forget claim timing.
- [ ] Tasks 2-25 — subsequent passes, dependency-ordered; schema-touching tasks (2,3,4,5) should land sequentially (shared `violations`/`submission.py` model files, alembic revision order), independent frontend-only tasks can parallelize in worktrees once their backend dependency lands.
