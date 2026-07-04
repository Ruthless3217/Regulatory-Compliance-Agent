---
type: Service
title: ComplianceEngine
description: The analysis entry point — claims a submission under a row lock, runs the LangGraph to END, applies the fail-closed persistability gate, and persists results atomically.
resource: backend/app/services/agents/compliance/engine.py
tags: [service, engine, orchestration, fail-closed]
timestamp: 2026-07-03T12:00:00Z
---

# ComplianceEngine

`ComplianceEngine.analyze_submission(submission_id, db)` in `backend/app/services/agents/compliance/engine.py` is the entry
point for grading a [submission](../data-model/submissions.md).

## Flow

1. **Claim** the submission under a row lock, set status `analyzing`.
2. Set the DB session into the graph `ContextVar` (`graph/context.py`) so nodes can reach the database.
3. **Run** the [LangGraph pipeline](../architecture/compliance-pipeline.md) to `END` in a single `ainvoke` (HITL disabled).
4. **Evaluate persistability** — the [fail-closed gate](../architecture/scoring-and-fail-closed.md): degraded/failed/no-content
   runs → status `needs_review` / `failed`, return `None` (never a passing grade).
5. **Persist** clean runs atomically: [ComplianceCheck](../data-model/compliance-checks.md) + all
   [Violations](../data-model/violations.md) + submission status `analyzed`, in one transaction. Severity/category normalized,
   confidence clamped [0,1] default 0.85, `rule_version` snapshotted.
6. `finally`: reset the graph context.

## Orchestrator

The graph is built by `ComplianceOrchestrator` (`backend/app/services/agents/orchestrator.py`), which manages checkpointing —
`AsyncRedisSaver` when Redis is available, else an in-memory `MemorySaver`. `thread_id = submission_id`.

## Related

- Invoked by the [Compliance API](../api/compliance.md) (`/compliance/analyze/{id}*`).
- Calls the [LLM service](llm-service.md) via the graph nodes.
