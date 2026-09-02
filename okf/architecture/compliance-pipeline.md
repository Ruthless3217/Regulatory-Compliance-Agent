---
type: Pipeline
title: Compliance Analysis Pipeline (LangGraph)
description: A strictly linear 6-node LangGraph state machine that chunks a document, retrieves evidence, grades it, checks disclosures, scores it, and finishes.
resource: backend/app/services/agents/orchestrator.py
tags: [architecture, langgraph, pipeline, nodes]
timestamp: 2026-07-03T12:00:00Z
---

# Compliance Analysis Pipeline

A **strictly linear** LangGraph (no conditional edges), built in `backend/app/services/agents/orchestrator.py` and executed by
the [Compliance engine](../services/compliance-engine.md):

```
START → preprocess → dispatch → analysis → disclosure → scoring → refinement → END
```

State is a shared `ComplianceState` TypedDict (`backend/app/services/agents/graph/state.py`); `violations`, `active_agents`, and
`messages` use `operator.add` reducers so node outputs **append** rather than overwrite. DB sessions reach nodes via a
`ContextVar` (`graph/context.py`), not serialized state.

## The six nodes

| Node | Role | What it does | LLM calls |
|------|------|--------------|-----------|
| **preprocess** | Librarian | Section-aware chunking via [ContextEngineeringService](../services/context-engineering-service.md); mirrors chunks into `rag_chunks` (non-fatal); resolves matched approved products | 0 |
| **dispatch** | Brain | Loads active [rules](../data-model/rules.md) (**fails closed** if unreachable); per-chunk retrieval of [rules](../rag/retrievers.md) (top-k 8) + **precedents** (top-k 15); enriches rules with regulator quotes; sets degraded flags | 0 |
| **analysis** | Specialist | Grades each chunk in parallel — the [three-tier grounding](three-tier-grounding.md) engine | 1–2 / chunk |
| **disclosure** | Checker | Deterministic mandatory-disclaimer verification via the [Disclaimer engine](../services/disclaimer-engine.md) | 0 (LLM backstop optional) |
| **scoring** | Scorer | Absolute-deduction score, critical cap, grade — see [Scoring & fail-closed](scoring-and-fail-closed.md) | 0 |
| **refinement** | HITL | No-op passthrough today (HITL is architected but disabled) | 0 |

## Concurrency & temperature

Chunks are graded under an `asyncio.Semaphore(grade_concurrency=2)` at **temperature 0**, with a one-shot corrective retry on
out-of-range indices. See [three-tier grounding](three-tier-grounding.md) for the per-chunk consolidation stages.

## Gotchas

- The `README.md` still describes a **5-node** graph on Gemini; the real graph is **6 nodes** (adds `disclosure`) on Azure
  gpt-5.4. The `nodes.py` module docstring also omits `disclosure_node`. `docs/ARCHITECTURE.md` is accurate.
- **HITL is disabled at compile time** (no `interrupt_before`); `refinement_node` is effectively a passthrough despite a stale
  docstring claiming otherwise. The dead resume machinery (`resume_workflow`/`get_state`) was deleted on 2026-09-02.
