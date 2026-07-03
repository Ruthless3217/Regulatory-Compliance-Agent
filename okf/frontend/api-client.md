---
type: Module
title: Frontend API Client & SSE
description: A single typed fetch module (lib/api.ts) with dual server/browser base-URL resolution, plus SSE-over-POST streaming (lib/sse.ts) for analysis progress and chat tokens.
resource: frontend/lib/api.ts
tags: [frontend, api-client, sse, fetch, types]
timestamp: 2026-07-03T12:00:00Z
---

# Frontend API Client & SSE

## `lib/api.ts`

A single typed fetch module organized by domain (submissions, compliance, rules, dashboard, health, knowledge base,
comparisons). Dual base-URL resolution: server-side uses `INTERNAL_API_BASE` (docker DNS); the browser uses
`NEXT_PUBLIC_API_BASE` directly or a same-origin `/api` proxy. `jsonFetch<T>` sets `cache: "no-store"` and throws on non-OK.
Multipart endpoints (`createSubmission`, `createComparison`, `generateRulesFromDocument`) build `FormData` manually.

## `lib/sse.ts` — SSE over POST

The backend streams from **POST** endpoints, so `EventSource` can't be used. `streamSSE(path, body, onEvent, signal)` fetches
with `Accept: text/event-stream`, reads `res.body.getReader()`, splits on `\n\n` frame boundaries, and parses `event:` / `data:`
lines. A `useSSEStream` hook runs it in an effect and auto-aborts on unmount.

## Two consumers

1. **Analysis stream** — `ReviewTab` subscribes to `/compliance/analyze/{id}/stream`; handles `stage` / `chunk` / `score` /
   `done` / `error`.
2. **Chat stream** — `ChatColumn` uses `streamSSE` directly against `/chat`, `/chat/quote-violation`, `/chat/suggest-rewrite`.

## Types

`lib/types.ts` — `Submission`, `Rule`, `Violation` (richest: grounding tier, precedent citation block, suppression), `Severity`,
`Category`, `SubmissionStatus`, `ComplianceResults`, dashboard/KB/compare types.

## Related

- Calls the [API](../api/index.md); rendered by the [pages](routing.md).
