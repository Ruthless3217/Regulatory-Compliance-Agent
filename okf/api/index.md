---
type: Section Index
title: API
description: The FastAPI REST + SSE surface — submissions, compliance analysis, rules, chat, dashboard, knowledge base, comparisons, similar, and RAG health. Paid LLM endpoints are guarded by rate-limit + budget.
resource: backend/app/api/routes/
tags: [api, fastapi, rest, sse]
timestamp: 2026-07-03T12:00:00Z
---

# API

FastAPI routers registered in `backend/app/main.py`, under `backend/app/api/routes/`. All paid LLM endpoints are guarded by
`Depends(llm_rate_limit)` + `Depends(llm_budget_guard)`.

## Documented routers

- [Compliance](compliance.md) — analyze (async/sync/**SSE stream**), results, feedback, reviewer score.
- [Rules](rules.md) — CRUD (versioned), generate-from-document, violation feedback.
- [Chat](chat.md) — RAG-grounded Q&A, quote-violation, suggest-rewrite (all SSE).
- [Knowledge base](knowledge-base.md) — ingest, stats, search, projection.

## Other routers (brief)

| Router | Key endpoints |
|--------|---------------|
| **submissions** | `POST /submissions` (text/upload), `GET/DELETE /submissions/{id}`, `GET /submissions/{id}/similar` |
| **dashboard** | `/dashboard/summary`, `/violations-by-category`, `/violations-by-severity`, `/timeseries`, `/top-rules` |
| **comparisons** | `POST /comparisons` (diff, no LLM), `GET/DELETE /comparisons/{id}` |
| **rag_health** | `GET /health/rag`, `POST /debug/rag/search` |
| app root | `GET /health`, `GET /` |

## Cross-cutting

- **Rate limit** — `backend/app/api/rate_limit.py` (Redis fixed-window, 30/min/principal, in-process fallback).
- **Budget** — `backend/app/services/llm_budget.py` (Redis day-keyed global token ceiling).
- **Auth** — none today; the `audit-trail/` plan proposes adding it.

## Related

- Interactive docs at `/docs` (Swagger) and `/redoc`. Consumed by the [frontend API client](../frontend/api-client.md).
