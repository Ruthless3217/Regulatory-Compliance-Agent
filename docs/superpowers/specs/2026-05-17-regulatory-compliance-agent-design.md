# Regulatory Compliance Agent — Design Spec

**Date:** 2026-05-17
**Owner:** ai.marketing@bajajlife.com
**Status:** Approved (brainstorming complete; awaiting user sign-off on this spec)

---

## 1. Purpose

Ship a polished, in-house regulatory-compliance review tool for the Bajaj Life marketing team. A user pastes or uploads marketing content (policy brochure, ad copy, web page, social post) and gets back:

1. **Inline highlighted violations** in the source text (Grammarly-style two-pane review),
2. A **shareable report** with score (0–100), letter grade (A–F), and violations grouped by severity/category,
3. A **chat interface** pinned to the submission for follow-up questions ("why is this a violation?", "rewrite this section in compliant language").

The product is inspired by the regulatory-compliance agent in [Thunderk3g/compliance-agent-poc](https://github.com/Thunderk3g/compliance-agent-poc), with three improvements: real side-by-side inline document review (the reference has none), a chat interface (reference has none), and Bajaj-branded editorial visual identity (reference is generic zinc).

## 2. Users & primary flow

**Primary user:** marketing / content team member.
**Primary flow ("paste-check-fix"):**
1. New analysis → paste/upload content → pick rule categories → submit
2. Watch progress stream in
3. Review violations inline; apply suggested fixes (copy to clipboard)
4. Switch to Chat tab for any follow-up questions
5. Export the report for sign-off / archival

**No auth in v1.** Behind corporate VPN / SSO. Firebase scaffolding in the backend stays dormant; wiring is a v2 task.

## 3. Scope

### In scope (v1)
- **Frontend** (new) — Next.js 15 + TypeScript + Tailwind + shadcn/ui, hosted on Vercel or in-house
- **Backend extensions** — Dockerfile, Alembic migrations + seed rules, `/chat` endpoint, SSE streaming on analysis
- **Rule corpus** — seeded IRDAI (~30), Bajaj brand (~20), SEBI (~15) rules; plus existing AI rule-generator for arbitrary regulator PDFs
- **Visual identity** — editorial / financial-publication aesthetic, white + Bajaj blue (#003595), Source Serif 4 headlines + Inter body + JetBrains Mono numerals

### Out of scope (v1, deferred to v2+)
Auth/RBAC, multi-tenant, real-time collaboration, in-place PDF auto-fix application, mobile-optimized layouts, LangSmith UI, HITL reviewer queue UI, i18n, telemetry, email/Slack notifications, submission comparison, rule versioning.

## 4. System architecture

```
┌─ Next.js 15 frontend (port 3000) ──────────────────────────────┐
│  App Router · TypeScript · Tailwind · shadcn/ui                │
│  app/                                                          │
│   ├── (workspace)/                                             │
│   │   ├── page.tsx                  → Submissions inbox        │
│   │   ├── new/page.tsx              → New submission           │
│   │   ├── submissions/[id]/         → Unified workspace        │
│   │   │     page.tsx                 → Review tab              │
│   │   │     report/page.tsx          → Report tab              │
│   │   │     chat/page.tsx            → Chat tab                │
│   │   ├── rules/                    → Rule library             │
│   │   ├── rules/generate/           → AI rule generator wizard │
│   │   ├── dashboard/                → Analytics                │
│   │   └── settings/                 → Project settings         │
│   └── api/                          → proxy-only, no biz logic │
│                                                                │
│  lib/api.ts    typed fetch client (zod-validated)              │
│  lib/sse.ts    Server-Sent-Event reader for streaming          │
└────────────────────────┬───────────────────────────────────────┘
                         │ REST + SSE (JSON)
┌────────────────────────▼───────────────────────────────────────┐
│  FastAPI backend (port 8000) — existing + 4 additions          │
│  + Dockerfile                                          (NEW)   │
│  + alembic/ + scripts/seed_rules.py                    (NEW)   │
│  + /chat endpoint                                      (NEW)   │
│  + /compliance/analyze/{id}/stream  (SSE)              (NEW)   │
└────────────────────────┬───────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
        PostgreSQL 15           Redis 7
        (+pgvector)         (LangGraph state)
                         │
              ┌──────────┴──────────┐
        Gemini 2.0 Flash      (optional) LangSmith
        via OpenAI-compatible
        API
```

The existing 5-node LangGraph workflow (preprocess → dispatch → analysis → scoring → refinement) is **untouched**. All four backend additions are isolated.

## 5. Frontend information architecture

### Sidebar — 240px fixed, no top nav

```
[ Bajaj Compliance ]   (serif wordmark)

WORKSPACE
  ◦ Submissions          /
  ◦ New analysis         /new

LIBRARY
  ◦ Rules                /rules
  ◦ Generate rules       /rules/generate

INSIGHTS
  ◦ Dashboard            /dashboard

SETTINGS
  ◦ Project settings     /settings
```

- Active item indicated by a 2px Bajaj-blue left bar (not a filled pill)
- Inactive: `text-muted-foreground`, hover `bg-muted`
- Footer: density toggle + API health dot

### Submission workspace — single canvas, three tabs

```
/submissions/[id]            Tab: REVIEW   (default)
/submissions/[id]/report     Tab: REPORT
/submissions/[id]/chat       Tab: CHAT
```

Sticky sub-header on all three tabs (`sticky top-0 z-20 bg-background/85 backdrop-blur-sm border-b`): breadcrumb · filename · overall score chip · tab strip · action cluster (Re-run, Export, Delete). Collapses to 56px on scroll.

#### REVIEW tab — two-pane

- **Left (~62%):** rendered document with `<mark>` highlights on offending phrases (color = severity). Click a highlight → scrolls and selects the matching ViolationCard on the right.
- **Right (~38%):** scrollable list of `ViolationCard`s. Top: filter chip bar (severity + category). Each card: severity-colored 2px left border, header row (severity badge, category badge, auto-fix sparkle badge, mono `#01` counter), evidence block with `<mark>` in a 3-line context window, emerald "SUGGESTED FIX" panel, action row ("Apply fix" copies to clipboard, "Dismiss").

#### REPORT tab — single scroll, no panes

- Hero block: large serif letter grade (A–F), numeric score in mono, three category chips (IRDAI / Brand / SEBI) with banded color (emerald ≥85, blue 70–84, amber 50–69, rose <50)
- KPI strip: total violations, critical count, auto-fixable count, est. fix time
- Violations grouped by severity (collapsible) then by category
- "Export PDF" button — server-side render via existing FastAPI

#### CHAT tab — pinned context

- Top pill bar: "Discussing: <filename> · N violations · <categories>"
- Standard chat column with streamed responses
- Quick-prompt footer: "Quote this violation", "Suggest rewrite", "Explain rule"
- Backend: `POST /chat` with `{submission_id, message, history}`, SSE response

### New analysis page (`/new`)

- Centered single card (~640px wide)
- Three input tabs: **Paste text** / **Upload file** / **Pull from URL**
- "Run with" — multi-select category chips (IRDAI / Brand / SEBI / All)
- Submit → routes to `/submissions/[id]` with SSE progress bar

### Rules pages

- `/rules` — table with filter chips, severity badges, activate/deactivate toggle, inline edit
- `/rules/generate` — two-step wizard: upload regulator PDF → review extracted rules in editable table → bulk-accept

### Dashboard

- 4-up KPI strip: submissions/week, violations caught, % auto-fixed, avg score
- Radar chart: this period vs prior period across categories
- Tabbed analytics: distribution / auto-fixability / severity heatmap
- Charts via Recharts

## 6. Visual system

### Palette (HSL CSS variables)

```css
/* Base */
--background          0 0% 100%;       /* pure white */
--surface             210 20% 99%;     /* card off-white */
--foreground          222 25% 12%;     /* near-black */
--muted               210 16% 96%;
--muted-foreground    215 12% 42%;
--border              214 20% 90%;     /* hairline */

/* Bajaj blue — single brand accent */
--primary             218 100% 29%;    /* #003595 */
--primary-fg          0 0% 100%;
--primary-50          218 80% 96%;
--primary-100         218 75% 90%;
--primary-500         218 100% 29%;
--primary-600         218 100% 23%;

/* Severity tokens (universal) */
--sev-critical        349 80% 50%;     /* rose-600 */
--sev-high            24 90% 53%;      /* orange-500 */
--sev-medium          38 92% 50%;      /* amber-500 */
--sev-low             218 100% 29%;    /* reuses primary blue */

/* Status (chips, soft fills) */
--success             152 60% 36%;     /* emerald-600 */
--warning             38 92% 50%;
--info                218 100% 29%;

--radius              6px;              /* 4px chips · 6px cards · 8px modals */
```

### Typography

| Role | Family | Weights | Notes |
|---|---|---|---|
| Headlines h1–h3 | **Source Serif 4** | 400 / 600 | Display italic for emphasis |
| Body, UI, labels | **Inter** | 400 / 500 / 600 | Tracking 0 |
| Numbers, IDs, scores | **JetBrains Mono** | 400 / 500 | Used aggressively |
| Micro-labels | Inter 10–11px | 600 | `uppercase`, `tracking-[0.06em]`, `text-muted-foreground` |

### Component patterns

- **Filter chip bar** — inverted-on-active (`bg-foreground text-background`), severity dot left, mono count right
- **ViolationCard** — 2px severity-colored left border, evidence block with `<mark>`, emerald "SUGGESTED FIX" panel
- **Score chip** — large serif grade + mono score, banded background
- **Buttons** — solid primary (Bajaj blue), outline secondary, ghost tertiary; 32px default height, 36px on hero CTAs
- **Cards** — white surface, 1px border, 6px radius, **no shadow**; hover `bg-muted/30`
- **Toasts** — top-right, 1px border in tone, slide-down 140ms; errors do not autohide

### Motion

- `slide-down` 140ms, `fade-in` 200ms. **That is all.** No spring, no parallax, no scroll-triggered reveals.

### Anti-patterns (forbidden)

Glassmorphism · neumorphism · heavy shadows · background gradients · gradient buttons or text · emoji in UI · stock illustrations · `rounded-2xl`/`-3xl` · multiple competing accents · animated hero shaders / three.js backgrounds.

### Density

Default = comfortable. `data-density="compact"` on `<html>` halves vertical padding on cards/tables. Toggle in settings.

## 7. Backend additions — detail

### 7.1 Dockerfile (`backend/Dockerfile`)

Base `python:3.11-slim`, install system deps for `psycopg2` + `pdfplumber` (`gcc`, `libpq-dev`, `poppler-utils`), copy `requirements.txt` first for layer caching, copy app, expose 8000, `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Unblocks `docker-compose up`.

### 7.2 Alembic migrations + seed (`backend/alembic/`, `backend/scripts/seed_rules.py`)

- `alembic.ini` + `env.py` wired to `Base.metadata` from `app/database.py`
- Initial migration `0001_initial.py` mirroring current `create_all()` schema (all 10 models)
- Seed script bulk-inserts:
  - **IRDAI** (~30 rules) — advertising guidelines, mis-selling, disclosure, ULIP risk-factor wording, return-projection language, claim-process disclaimers
  - **Bajaj brand** (~20 rules) — prohibited superlatives, tone of voice, mandatory taglines/disclaimers, terminology
  - **SEBI** (~15 rules) — investment-product wording, mutual-fund-style return disclaimers, past-performance language
- Seed source file = YAML so non-engineers can extend
- Run order: `alembic upgrade head` → `python -m scripts.seed_rules`

### 7.3 `/chat` endpoint (`backend/app/api/routes/chat.py`)

```
POST /chat                                Content-Type: application/json
Body: {
  submission_id: UUID,
  message: str,
  history: [{role: "user"|"assistant", content: str}]
}
Response: text/event-stream
  event: token   data: "..."              streamed deltas
  event: done    data: {tokens_used, model}
  event: error   data: {message}
```

- Prompt assembly: system message ("regulatory-compliance assistant for Bajaj Life, reviewing submission X"), full submission text, active rules grouped by category, existing violations as JSON
- Uses existing `LLMService` with `stream=True`
- Stateless on server side — history is client-managed, `submission_id` is the persistent anchor
- Quick-prompt convenience routes (`/chat/quote-violation`, `/chat/suggest-rewrite`) are sugar that pre-fill the user message before delegating to the same handler

### 7.4 SSE streaming on analysis (`backend/app/api/routes/compliance.py`)

New route alongside existing sync/async analyze:

```
POST /compliance/analyze/{id}/stream
Response: text/event-stream
  event: stage    data: {stage: "preprocess"|"dispatch"|"analysis"|"scoring", progress: 0.0–1.0}
  event: chunk    data: {chunk_index, category, violations: [...]}
  event: score    data: {overall_score, grade, scores: {...}}
  event: done     data: {check_id}
  event: error    data: {message}
```

- Implementation: wraps existing `ComplianceEngine.analyze_submission` with an `asyncio.Queue` that LangGraph nodes write progress events to
- Existing sync/async endpoints are not modified

## 8. Data flow

**New analysis (paste/upload → result):**

```
[UI /new] ──POST /submissions── [API] ── creates Submission row
[UI redirects to /submissions/{id}]
   │
   └── opens EventSource on /compliance/analyze/{id}/stream
         │
         ├── event: stage   → progress bar updates
         ├── event: chunk   → ViolationCards stream in on right pane
         ├── event: score   → score chip animates in header
         └── event: done    → progress bar closes, final state cached
```

**Chat:**

```
[UI /submissions/{id}/chat] ──POST /chat── [API]
         │
         └── opens EventSource
               ├── event: token  → message bubble updates
               └── event: done   → footer enables for next message
```

**Apply suggested fix:** client-side only in v1 — copies the suggested fix text to clipboard, no document mutation.

## 9. API surface — full list

Existing (unchanged):
- `POST/GET/DELETE /submissions/`
- `POST /compliance/analyze/{id}` and `/sync`
- `GET /compliance/results/{id}`
- `GET /compliance/check/{check_id}`
- `POST /compliance/resume/{id}`
- `POST/GET/PATCH/DELETE /rules/`
- `POST /rules/generate-from-document`
- `GET /dashboard/summary`, `/violations-by-category`, `/violations-by-severity`
- `GET /health`, `GET /`

New:
- `POST /compliance/analyze/{id}/stream` — SSE
- `POST /chat` — SSE
- `POST /chat/quote-violation`, `POST /chat/suggest-rewrite` — SSE convenience routes

## 10. Project structure (target)

```
D:\Regulatory-Compliance-Agent\
├── backend/                               (existing — see file inventory)
│   ├── Dockerfile                         (NEW)
│   ├── alembic/                           (NEW)
│   │   ├── env.py
│   │   └── versions/0001_initial.py
│   ├── scripts/                           (NEW)
│   │   ├── seed_rules.py
│   │   └── seeds/
│   │       ├── irdai.yaml
│   │       ├── bajaj_brand.yaml
│   │       └── sebi.yaml
│   └── app/api/routes/
│       └── chat.py                        (NEW)
│   └── app/api/routes/compliance.py       (MODIFIED — add /stream)
├── frontend/                              (NEW — entire tree)
│   ├── app/
│   │   ├── (workspace)/
│   │   │   ├── layout.tsx                 (sidebar shell)
│   │   │   ├── page.tsx                   (Submissions inbox)
│   │   │   ├── new/page.tsx
│   │   │   ├── submissions/[id]/
│   │   │   │   ├── layout.tsx             (sticky sub-header + tabs)
│   │   │   │   ├── page.tsx               (Review)
│   │   │   │   ├── report/page.tsx
│   │   │   │   └── chat/page.tsx
│   │   │   ├── rules/page.tsx
│   │   │   ├── rules/generate/page.tsx
│   │   │   ├── dashboard/page.tsx
│   │   │   └── settings/page.tsx
│   │   ├── globals.css                    (tokens)
│   │   └── layout.tsx                     (font wiring)
│   ├── components/
│   │   ├── ui/                            (shadcn primitives)
│   │   ├── workspace/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── SubmissionHeader.tsx
│   │   │   └── DensityToggle.tsx
│   │   ├── review/
│   │   │   ├── DocumentPane.tsx
│   │   │   ├── ViolationsPane.tsx
│   │   │   ├── ViolationCard.tsx
│   │   │   ├── FilterChipBar.tsx
│   │   │   └── highlightMarkup.ts
│   │   ├── report/
│   │   │   ├── ScoreHero.tsx
│   │   │   ├── KPIStrip.tsx
│   │   │   └── ViolationGroup.tsx
│   │   ├── chat/
│   │   │   ├── ChatColumn.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── PinnedContextBar.tsx
│   │   │   └── QuickPromptFooter.tsx
│   │   ├── rules/
│   │   │   ├── RulesTable.tsx
│   │   │   └── RuleGeneratorWizard.tsx
│   │   └── dashboard/
│   │       ├── KPICards.tsx
│   │       ├── CategoryRadar.tsx
│   │       └── SeverityHeatmap.tsx
│   ├── lib/
│   │   ├── api.ts                         (typed fetch + zod)
│   │   ├── sse.ts                         (SSE reader hook)
│   │   ├── types.ts                       (mirrors backend Pydantic)
│   │   └── format.ts                      (date, number, grade)
│   ├── tailwind.config.ts
│   ├── components.json                    (shadcn)
│   ├── package.json
│   └── tsconfig.json
├── docs/superpowers/specs/
│   └── 2026-05-17-regulatory-compliance-agent-design.md  (this file)
├── docker-compose.yml                     (MODIFIED — add frontend service)
└── README.md                              (MODIFIED — add frontend section)
```

## 11. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| SSE proxying through corporate network strips events | Medium | Fall back to long-polling `/compliance/results/{id}` every 1.5s if EventSource fails to receive `stage` event within 5s |
| Highlighting inline in rendered PDF/DOCX is hard | High | v1 normalizes everything to plain text/markdown before display; original file is downloadable but not the rendered surface. Set expectations explicitly. |
| Seed rules drift from real IRDAI/SEBI guidance | High | Mark seed rules `is_auto_generated=false`, `confidence_score=0.9`, with `generation_source: "seed/irdai-2024-q1"`. Compliance team reviews quarterly. |
| Gemini 2.0 Flash rate limits during streaming | Low | Existing `LLMService` already has retry; surface "rate-limited, retrying" toast in UI rather than failing silently. |
| User pastes a 100k-token document | Medium | Frontend caps input at 50k chars with a warning; backend already chunks via tiktoken. |

## 12. Success criteria

- A marketing user can paste/upload a sample brochure and reach a complete violation report in ≤30 seconds for a 2-page document
- Review tab: clicking a highlight in the document scrolls the correct ViolationCard into view in ≤200ms
- Chat tab: streamed response begins within ≤1.5s of submitting a message
- Visual identity: a non-engineer reviewer cannot tell the UI was AI-generated; the editorial / financial-publication feel is recognizable
- `docker-compose up` brings the entire stack (Postgres + Redis + backend + frontend) to ready in under 90 seconds on a developer laptop
- Seed rules cover the top 60+ rules across IRDAI, Bajaj brand, and SEBI before first internal demo

## 13. Open questions (resolve before implementation)

None blocking. The following will be resolved during implementation as defaults:
- Exact font sourcing (Google Fonts vs self-hosted) — default Google Fonts
- Recharts vs Visx for dashboard — default Recharts
- PDF rendering library for Report Export — default Playwright server-side via existing FastAPI worker

---

**Next step:** invoke `writing-plans` skill to break this spec into an implementation plan.
