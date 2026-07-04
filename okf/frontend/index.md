---
type: Section Index
title: Frontend
description: The Next.js 15 App Router frontend — routing under the (workspace) group, a typed API client with SSE-over-POST, and feature UIs for review, report, chat, dashboard, knowledge base, and compare.
resource: frontend/
tags: [frontend, nextjs, react, typescript, tailwind]
timestamp: 2026-07-03T12:00:00Z
---

# Frontend

`frontend/` — Next.js 15 (App Router, `output: "standalone"`), React 19, TypeScript (strict), Tailwind 3.4 + Radix/shadcn
primitives, Recharts, react-markdown. Package `bajaj-compliance-frontend`.

## Concepts

- [Routing](routing.md) — the `(workspace)` route group and every page.
- [API client](api-client.md) — `lib/api.ts` typed fetch + `lib/sse.ts` SSE-over-POST.

## Design system

Token-based HSL CSS vars (single "Bajaj blue" brand), a 4-tier severity scale, Inter + JetBrains Mono, shadcn-pattern
`components/ui/*`, a density toggle. Severity normalization (`lib/format.ts`) collapses the backend's two vocabularies into 4
canonical buckets; `lib/highlightMarkup.ts` mirrors the backend `_normalize_ws` to place `<mark>` highlights.

## State

Deliberately lightweight — no Redux/Zustand/React Query. Two React Contexts: `SubmissionWorkspaceContext` (shared violations +
selection) and `CommandPaletteProvider` (⌘K).

## Related

- Talks to the [API](../api/index.md) via REST + SSE; deploy config in [deployment](../config/deployment.md).
