---
type: API Router
title: Compliance API
description: Trigger and retrieve compliance analysis — async, synchronous, and SSE-streaming variants, plus results, per-check details, violation feedback, and held-out reviewer scores.
resource: backend/app/api/routes/compliance.py
tags: [api, compliance, analysis, sse]
timestamp: 2026-07-03T12:00:00Z
---

# Compliance API

`backend/app/api/routes/compliance.py`, prefix `/compliance`. Analyze endpoints are guarded by `llm_rate_limit` +
`llm_budget_guard` and call the [Compliance engine](../services/compliance-engine.md).

| Method · Path | Purpose |
|---------------|---------|
| `POST /compliance/analyze/{id}` | Async analysis (FastAPI `BackgroundTasks`) |
| `POST /compliance/analyze/{id}/sync` | Synchronous — returns score/grade/status; `None` result surfaces a degraded status |
| `POST /compliance/analyze/{id}/stream` | **SSE** — polls the DB every 0.7s, emits `stage` / `chunk` / `score` / `done` / `error` events |
| `GET /compliance/results/{id}` | Latest check + serialized [violations](../data-model/violations.md) |
| `GET /compliance/check/{check_id}` | One check's summary |
| `POST /compliance/violations/{id}/actions` | Reviewer verdict (the live UI path) → adaptive [rule reliability](../architecture/scoring-and-fail-closed.md) + a precedent in the removable "Reviewer feedback" corpus layer |
| `POST /compliance/violations/{id}/feedback` | Legacy accept/reject shim (no frontend callers) → rule reliability only |
| `POST /compliance/check/{id}/reviewer-score` | Held-out reviewer score (**eval only**, never trained on) |

## SSE contract

The stream is consumed by the frontend [Review UI](../frontend/routing.md): `stage` drives the progress bar, `chunk`
appends deduped [violations](../data-model/violations.md), `score` sets the score/grade, `done` triggers a refresh.

## Related

- Runs the [pipeline](../architecture/compliance-pipeline.md); persists behind the
  [fail-closed gate](../architecture/scoring-and-fail-closed.md).
