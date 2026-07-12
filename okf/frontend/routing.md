---
type: Reference
title: Frontend Routing & Pages
description: The App Router structure — a root layout, the (workspace) group layout with sidebar/topbar/command-palette, and pages for submissions, analysis, dashboard, knowledge base, rules, settings, and compare.
resource: frontend/app/
tags: [frontend, routing, pages, app-router]
timestamp: 2026-07-03T12:00:00Z
---

# Frontend Routing & Pages

Two nested layouts: the root (`app/layout.tsx`, fonts + density cookie + toaster) and the workspace group
(`app/(workspace)/layout.tsx`, sidebar + topbar + command palette). The `(workspace)` group name is not part of the URL.

| Route | Renders |
|-------|---------|
| `/` | Submissions inbox — grouped tables + KPI strip + activity rail |
| `/new` | New analysis — paste / upload / URL intake |
| `/submissions/[id]` | **Review** tab — inline `<mark>` highlights + violation cards + live SSE progress |
| `/submissions/[id]/report` | **Report** tab — score hero, KPI strip, grouped violations, print/PDF |
| `/submissions/[id]/chat` | **Chat** tab — streamed Q&A grounded in the submission |
| `/rules` | Rules library — filter, activate/deactivate, inline edit |
| `/rules/generate` | AI rule extraction wizard |
| `/dashboard` | KPIs + category radar + severity donut + timeseries |
| `/knowledge-base` | 2-D vector-space scatter + precedent search |
| `/settings` | Density toggle, API base URL, health check, version |
| `/compare`, `/compare/new` | Standalone document diff — list / upload (in the `(workspace)` shell) |
| `/compare/[id]` | Full-screen **Compare viewer** — in the `(viewer)` route group (no sidebar/topbar); opened in a new browser tab |

## Feature UIs

- **Review** (`components/review/*`) — `DocumentPane` (highlights) + `ViolationsPane` (cards, suppressed "needs review" lane).
- **Report** (`components/report/*`) — print-only styles, "analysis incomplete → NOT graded compliant" banner, `ScoreHero`.
- **Chat** (`components/chat/*`) — streamed tokens, markdown rendering, quick-prompt footer (quote / rewrite / explain).
- **Compare** — full-screen `components/compare-viewer/*` (`ViewerShell` + `ViewerContext`, `Toolbar`, `PagePane` ×2,
  `TextRedline`, `HeatStrip`, `ChangesPanel`/`ChangeCard`, `ExportPopover`, `AdjustComparisonPopover`). The old
  `components/compare/*` (`DiffViewer`, `ChangesPane`) is reused by `TextRedline` / the list page.
- **Knowledge base** (`components/knowledge-base/*`) — Recharts scatter + precedent search.

## Related

- Data fetched via the [API client](api-client.md) from the [API](../api/index.md).
