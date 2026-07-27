# Frontend Studio — Full-App Reskin (Design Sandbox)

**Date:** 2026-07-20
**Status:** Design approved (pending written-spec review)
**Owner:** Bajaj Life marketing/AI team
**Topic:** A fresh, isolated UI workspace that reskins the entire Regulatory Compliance Agent, built against mock data.

---

## 1. Goal

Rebuild the *entire* Regulatory Compliance Agent front end with a world-class "refined enterprise SaaS" look, in a **closed, isolated workspace** so the design can be explored and judged without touching the production `frontend/` or `backend/`. Same feature surface, dramatically better visual design and UX.

This first phase is a **static design sandbox**: every screen renders from realistic mock fixtures. It exists to lock the visual language across the whole surface. Wiring the approved UI to the live backend is a deliberate, separate later phase.

## 2. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Scope | **Full-app reskin** — every existing surface, features preserved |
| 2 | Component strategy | **React/Next + Tailwind**; pull 21st.dev blocks natively (shadcn registry); **Basecoat as the design-system/token layer**, its components ported into React |
| 3 | Aesthetic | **Refined enterprise SaaS** (evolves the V3 direction), Bajaj-blue anchored, data-dense, quiet precision |
| 4 | Theming | **Light + dark**, both fully tuned; disciplined palette (Bajaj blue + refined neutrals + semantic severity colors); no decorative accents |
| 5 | Workspace | **Static design sandbox, mock data**; standalone app in a new folder; no backend wiring in this phase |
| 6 | Inspiration | **Linear + Vercel/Geist** — crisp, quiet, keyboard-first, monochrome + one accent, mono details, command palette |
| 7 | Sequencing | **Core wave first, then expand** the same language to the rest |
| 8 | Folder name | **`frontend-studio/`** |
| 9 | Typeface | **Inter (sans) + JetBrains Mono (mono)** — no new font dependency |

## 3. Architecture

### 3.1 Isolation
A new top-level folder **`frontend-studio/`** holding its own standalone Next.js 15 / React 19 / TypeScript app: own `package.json`, own `node_modules`, own dev port (**3100**). Nothing in `frontend/` or `backend/` is modified. Stack mirrors the current app (Next 15, React 19, Tailwind 3, shadcn base) so a future swap-in is low-friction.

*Rejected alternative:* a route-group inside `frontend/` (`app/(studio)`). It would share `node_modules`, config, and globals — not the closed workspace requested.

### 3.2 Mock-data layer (the key architectural move)
The sandbox never calls the backend. Instead:

- **`lib/types.ts`** is copied **verbatim** from `frontend/lib/types.ts` so fixtures and components are type-checked against the real data shapes.
- **`lib/mock/`** holds typed fixtures (see §7).
- **`lib/mockApi.ts`** mirrors the **exact function signatures of the real `frontend/lib/api.ts`** (e.g. `listSubmissions()`, `getComplianceResults(id)`, `createSubmission(body)`, `getDashboardSummary()`, `listComparisons()`, the `super_admin` readers, etc.) but returns fixtures wrapped in a small artificial-latency helper. Streaming endpoints (`/compliance/analyze/{id}/stream`, `/chat`) are simulated with an **async generator** that emits the real SSE event shapes (`stage` → `chunk` → `score` → `done`; `token` → `done`).

Because `mockApi` matches the real client 1:1, later wiring = replacing the import source, not rewriting screens.

### 3.3 Directory structure
```
frontend-studio/
  app/
    (workspace)/            # analyst surface: dashboard, new, submissions, rules, knowledge-base, compare, settings
    (super-admin)/          # users, usage, runs, sessions, audit, rules-audit
    (viewer)/               # read-only compare
    login/  account/        # auth screens (visual only)
    layout.tsx  globals.css
  components/
    ui/                     # Basecoat-derived primitives (button, card, dialog, tabs, badge, input, ...)
    shell/                  # sidebar, top bar, command palette, theme toggle, role switcher
    <domain>/               # dashboard/, review/, report/, chat/, compare/, rules/, knowledge-base/, admin/
  lib/
    types.ts                # copied verbatim from frontend/
    mock/                   # fixtures
    mockApi.ts              # signature-compatible mock client
    utils.ts  format.ts
  components.json           # shadcn registry config (for 21st.dev pulls)
  tailwind.config.ts  postcss.config.mjs  tsconfig.json  next.config.ts
```

## 4. Design system

### 4.1 Token contract
Adopt the **canonical shadcn/Basecoat CSS-variable set** so Basecoat recipes and 21st.dev blocks theme cleanly without fighting custom names. This replaces the current app's `--surface` / `--primary-50` naming.

Core tokens (both `:root` light and `.dark`):
`--background --foreground --card --card-foreground --popover --popover-foreground --primary --primary-foreground --secondary --secondary-foreground --muted --muted-foreground --accent --accent-foreground --border --input --ring --destructive --destructive-foreground --radius`

Preserved domain tokens (carried from the current app so severity/status semantics survive):
`--sev-critical --sev-high --sev-medium --sev-low` and `--success --warning --info`.

Palette intent:
- **Primary** = Bajaj blue (evolve current `hsl(218 100% 29%)`), brightened in dark mode for contrast.
- **Neutrals** = a refined gray scale (Linear/Vercel-like); light = white bg / near-white cards / hairline borders; dark = near-black bg (~zinc-950, not pure black) / slightly elevated cards / low-contrast borders.
- **Severity** = keep current hues (critical red, high orange, medium amber, low blue).
- Exact hex/HSL tuning happens during implementation; both themes are hand-tuned, not auto-inverted.

### 4.2 Typography
- **Sans:** Inter (via `next/font`), variable `--font-sans`.
- **Mono:** JetBrains Mono, variable `--font-mono` — used deliberately for the "instrument layer": scores, grades, percentages, IDs, similarity/confidence numbers, regulator quotes, code.
- Type scale tuned for density (compact table/label sizes) with clear hierarchy.

### 4.3 Form & motion language
Tight radii (~6–8px), hairline borders favored over heavy shadows, generous whitespace, crisp focus rings, quiet micro-motion (140–200ms ease-out; `framer-motion` where a 21st block needs it). First-class **empty / loading (skeleton) / error / degraded** states are part of the system, not afterthoughts.

## 5. App shell & navigation

Persistent **left sidebar + slim top bar**. Sidebar is role-aware with three sections mirroring RBAC: **Workspace** (user/analyst), **Super-admin**, **Viewer**. Top bar carries breadcrumbs/title, theme toggle (light/dark/system), and a global search entry.

- **⌘K command palette** (`cmdk`): jump to submissions, run actions, switch pages.
- **Sandbox role switcher:** a mock "current user + role" control (no real auth) so every role's navigation and gating can be previewed. Roles from the backend matrix: `user`, `admin`, `super_admin`, plus a read-only `viewer` view.

## 6. Screen inventory & wave plan

Full surface, grouped. **★ = Wave 1** (built first to judge the look); the rest inherit the locked language in Wave 2.

**Workspace (analyst)**
- ★ **Dashboard** — KPI cards, score trend, category radar, severity donut/heatmap, top-violated-rules, recent submissions.
- ★ **New submission** — paste/upload, **document-type classify → confirm → gate** (product vs editorial; UIN/mandatory-element gating), then analyze. Includes the simulated **analysis progress stream** (stage → chunk → score).
- **Submissions list** — table with status/approval/score, filters.
- ★ **Submission → Review** — document pane with inline severity highlights + overlap-group marks; violations pane with **filter chips** (severity/category/tier), **violation cards** showing reviewer-voice tags, grounding tier (precedent/rule/novel/product-fact), confidence, regulator quote, precedent citation (anchor/verbatim/final-text/similarity), suggested fix; **accept/reject feedback**; a separate **"Needs review"** lane for suppressed/structural findings.
- ★ **Report** — score hero (grade + overall + per-category sub-scores), KPI strip, violation **groups**, PDF-export affordance.
- ★ **Chat** — RAG-grounded assistant anchored to a submission (simulated token streaming), plus quote-violation and suggest-rewrite quick actions; pinned-context bar.
- **Rules** — rules table (category/severity/active/version/adaptive-weight θ), create/edit, **generate-from-document wizard**.
- **Knowledge Base** — precedent search results + **2D vector-space scatter** (UMAP/PCA projection).
- **Compare** — cross-format docx↔pdf **diff viewer** (word-level redline), pixel/page overlay, changes panel, per-change annotations, export menu (changes-report.docx / highlighted PDFs / side-by-side / bundle.zip), adjust/rerun.
- **Settings** — preferences, density, theme.

**Super-admin console**
- **Users** (roster + lifetime runs + spend, create/manage), **Usage/cost** (summary, by-document, timeseries, CSV export), **Runs** (analysis-run ledger), **Sessions** (login sessions + active-time), **Audit** (append-only event feed), **Rules audit** (rule-change timeline).

**Auth / account**
- **Login**, **Change password** (visual only — no real auth).

**Viewer**
- **Read-only compare** viewer.

## 7. Mock data / fixtures

Fixtures are designed to exercise the states that make a compliance tool feel real:

- **A richly-violated submission**: violations spanning all four severities and all four grounding tiers (precedent citation, rule finding, novel finding, product-fact finding), with reviewer-voice `violation_metadata` (action_type/evidence_needed/regulatory_basis), precedent provenance (anchor text, verbatim comment, cited final text, similarity score), a **suppressed "needs-review"** subset, and **overlap groups** (`group_id` + one `is_primary`).
- **A scored check**: grade A–F, overall score, per-category sub-scores, compliance status.
- **Dashboard aggregates**: summary totals, timeseries, violations-by-category, violations-by-severity, top rules.
- **A cross-format comparison**: `DiffBlock[]` (equal/insert/delete/replace with word-level diffs + moves), render overlay boxes, annotations.
- **Admin ledgers**: user rows, usage rows, run rows, session rows, audit rows, rule-audit rows.
- **Document-type + gating** examples (product_marketing vs blog_article) to demo the New-submission gate.

Every screen ships explicit **empty / loading / error / degraded (needs_review)** variants.

## 8. Component sourcing plan

- **Basecoat = design-language layer.** Its token system and component recipes (button, card, dialog, tabs, badge, input, dropdown, alert, popover, tooltip, table) are ported into small React primitives under `components/ui/*`. This guarantees a cohesive, on-system look and gives us the shadcn-standard tokens both libraries expect.
- **21st.dev = higher-order React blocks**, pulled natively via the shadcn registry (`npx shadcn add "https://21st.dev/r/…"`) for stat/KPI cards, data tables, nav/sidebar, command menu, hero/empty states — then re-themed to our tokens.
- **Support libs** (proven in `frontend/`): `recharts`, `@tanstack/react-virtual`, `lucide-react`, `sonner`. **Add:** `cmdk` (command palette) and `framer-motion` (motion for select 21st blocks).

## 9. Non-goals (explicit)

- No real backend calls, no real auth/RBAC enforcement, no real SSE, no DB/migrations.
- No changes to `frontend/` or `backend/`.
- Not a production deploy; this is a visual prototype to lock the design language.

## 10. Verification (this phase)

Static-sandbox appropriate, consistent with the standing "no live-API tests" rule:
- `tsc --noEmit` typechecks clean.
- `next lint` passes.
- `next build` succeeds.
- Each Wave-1 screen renders from fixtures with no runtime errors, in both light and dark themes.

## 11. Future phase (out of scope here)

Once the look is approved: replace `mockApi` imports with the real `api` client, restore auth/RBAC, real SSE, and reconcile the token rename — then evaluate swapping `frontend-studio/` in for `frontend/`.

## 12. Risks / open items

- **21st.dev pulls** require the shadcn CLI and network access at build-authoring time; if unavailable, the equivalent block is hand-built from Basecoat primitives (same tokens, same look).
- **Token rename** (`--surface` → shadcn set) is intentional and confined to the new app; it does not affect `frontend/`.
- **Design fidelity vs. mock data**: fixtures must be rich enough that the reskin's data-dense screens (review, dashboard, admin) read as real; §7 covers this.
