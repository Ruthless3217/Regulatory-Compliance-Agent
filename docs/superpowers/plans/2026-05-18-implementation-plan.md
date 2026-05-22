# Regulatory Compliance Agent — Implementation Plan

**Date:** 2026-05-18
**Spec:** `docs/superpowers/specs/2026-05-17-regulatory-compliance-agent-design.md`

Four phases. Each phase ends with shippable, testable software.

---

## Phase A — Backend additions

**Goal:** Existing FastAPI + LangGraph backend gets four additions; existing endpoints + workflow untouched.

1. **Schema fixes (preconditions)**
   - `ViolationSchema`: add `id: str | None` field so the contract matches the JSON response in `compliance.py:140`.
   - `LLMService`: add `stream_response(prompt, system_prompt, history) -> AsyncIterator[str]` using `client.chat.completions.create(stream=True)`. Needed by `/chat` and `/compliance/.../stream`.
2. **Dockerfile** — existing image is fine but missing `poppler-utils` for `pdfplumber`. Add it.
3. **Alembic** — `alembic.ini`, `alembic/env.py` wired to `Base.metadata`, `versions/0001_initial.py` mirroring current `create_all()` for all 10 models. Remove `Base.metadata.create_all()` from `main.py` lifespan (migrations now own schema).
4. **Seed rules** — YAML at `backend/scripts/seeds/{irdai,bajaj_brand,sebi}.yaml`; loader at `backend/scripts/seed_rules.py`. Seeds tagged `is_auto_generated=false`, `confidence_score=0.9`, `generation_source="seed/<reg>-2024-q1"`. Category strings: `irdai` / `brand` / `sebi` (new canonical values).
5. **`POST /chat`** — `backend/app/api/routes/chat.py`. SSE response with `token` / `done` / `error` events. Stateless; client manages history; `submission_id` anchors context. System prompt injects full submission text + active rules + existing violations as JSON. Convenience routes `/chat/quote-violation` and `/chat/suggest-rewrite`.
6. **`POST /compliance/analyze/{id}/stream`** — added to `backend/app/api/routes/compliance.py`. Wraps `ComplianceEngine.analyze_submission` with an `asyncio.Queue` that LangGraph nodes write progress to. Events: `stage` / `chunk` / `score` / `done` / `error`. Existing sync/async endpoints unchanged.

**Acceptance:** `docker-compose up` brings backend healthy. `curl -N -X POST http://localhost:8000/chat -d '{"submission_id":"...","message":"hi","history":[]}'` streams tokens. Pytest passes.

---

## Phase B — Frontend foundation

**Goal:** Clickable Next.js shell connected to the backend, with Submissions inbox + New analysis working end-to-end.

1. **Scaffold** — `frontend/` (Next.js 15, TS strict, App Router, Tailwind, shadcn/ui `new-york`, pnpm or npm).
2. **Tokens & fonts** — `app/globals.css` with HSL palette from spec §6; `app/layout.tsx` wires Source Serif 4 + Inter + JetBrains Mono via `next/font/google`; `data-density` attribute on `<html>` from cookie.
3. **Lib** — `lib/api.ts` (typed fetch + zod, wraps all 19 endpoints from spec §9), `lib/sse.ts` (`useSSE` hook with 5s timeout → long-poll fallback), `lib/types.ts` (mirrors backend Pydantic), `lib/format.ts`.
4. **Shadcn primitives** — button, card, input, textarea, badge, tabs, dialog, dropdown-menu, sonner, separator, skeleton, scroll-area, tooltip. Overrides for no-shadow cards.
5. **Workspace shell** — `app/(workspace)/layout.tsx` (240px sidebar + main), `components/workspace/Sidebar.tsx` (2px blue active bar), `DensityToggle.tsx`, `ApiHealthDot.tsx`, `SubmissionHeader.tsx`.
6. **Pages (functional)** — Submissions inbox at `/`, New analysis at `/new` (Paste/Upload/URL tabs, category chips, submit → POST /submissions → SSE redirect).
7. **Pages (shell)** — `submissions/[id]/layout.tsx` with sticky sub-header + tab strip; three placeholder tab pages (Plan C fills).

**Acceptance:** `pnpm dev` boots; sidebar renders; can paste content and reach `/submissions/[id]` with no errors. Visual matches spec §6 (white + blue, no shadows, serif headlines).

---

## Phase C — Submission workspace tabs

**Goal:** Three fully functional tabs — Review, Report, Chat.

1. **Shared context** — `SubmissionWorkspaceContext` provider in `submissions/[id]/layout.tsx` holding `submission`, `violations`, `selectedViolationId`. All tabs read from it.
2. **Review tab**
   - `lib/highlightMarkup.ts` — pure `applyHighlights(text, violations) → HTML string` (overlapping = highest severity wins; HTML-escaped). Unit-tested.
   - `DocumentPane` (~62%) — renders highlighted text; click `<mark>` selects matching card.
   - `ViolationsPane` (~38%) — `FilterChipBar` + scrollable `ViolationCard` list.
   - `ViolationCard` — 2px severity left border, severity/category badges, auto-fix sparkle, `#01` mono counter, evidence with `<mark>`, emerald "SUGGESTED FIX" panel, "Apply fix" (clipboard) + "Dismiss".
   - SSE wiring: if submission is still analyzing, open `/compliance/analyze/{id}/stream` and append chunks.
3. **Report tab**
   - `ScoreHero` — 120px serif grade, mono score, three banded category chips.
   - `KPIStrip` — totals/critical/auto-fixable/est. fix time (~12 wpm).
   - `ViolationGroup` — collapsible severity→category.
   - `ExportPdfButton` — `window.print()` + route-scoped `print.css`.
4. **Chat tab**
   - `PinnedContextBar` — "Discussing: <filename> · N violations · <categories>".
   - `ChatColumn` + `MessageBubble` — optimistic user bubble + empty assistant bubble + SSE token append via `useSSE`.
   - `QuickPromptFooter` — Quote / Rewrite / Explain rule (disabled until a violation is selected).

**Acceptance:** End-to-end golden path works — paste content → analysis streams in → highlights are clickable → cards selectable → report renders → chat streams responses. Spec §12 performance targets met (highlight scroll ≤200ms; chat starts ≤1.5s).

---

## Phase D — Supporting pages + polish

**Goal:** Round out v1 — rules library, rule generator wizard, dashboard, settings, container/docs.

1. **Rules library** — `/rules` table (filter chips, severity badges, inline activate/deactivate, edit). `RulesTable.tsx` extracted for testability.
2. **Rule generator wizard** — `/rules/generate` two-step: upload PDF → `generateRulesFromDocument()` → review extracted rules in editable table → bulk-accept.
3. **Dashboard** — `/dashboard` — `KPICards`, `CategoryRadar` (Recharts), `SeverityHeatmap` (custom Recharts), `AnalyticsTabs`. Theme overridden to single-blue palette + severity tokens.
4. **Settings** — `/settings` — Appearance (density), API (base URL + health button), About (version + build SHA).
5. **Container & docs** — `frontend/Dockerfile` (Node 20 multi-stage), add `frontend` service to `docker-compose.yml`, update `README.md` with Frontend section.
6. **Polish** — Sonner toast wiring (if not done in B), print stylesheet finalization, Playwright `golden-path.spec.ts` covering spec §12 success criteria.

**Acceptance:** `docker-compose up` brings entire stack to ready in <90s. Playwright golden path green. Non-engineer reviewer cannot tell the UI was AI-generated.

---

## Cross-cutting constraints

- **Existing LangGraph workflow untouched** — wrap, don't modify.
- **Visual identity:** white + Bajaj blue `#003595` only; severity tokens (rose/orange/amber/blue) for violations only; Source Serif 4 + Inter + JetBrains Mono; 1px borders; 6px radius; no shadow; only `slide-down` 140ms / `fade-in` 200ms; **forbidden:** glassmorphism, gradients, emoji, `rounded-2xl`/`-3xl`.
- **Categories canonical:** `irdai` / `brand` / `sebi` for new content; existing `regulatory` / `seo` strings continue to work (no migration needed).
- **No auth in v1** (behind corporate VPN/SSO).
