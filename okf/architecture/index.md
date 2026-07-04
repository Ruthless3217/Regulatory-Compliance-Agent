---
type: Section Index
title: Architecture
description: The compliance analysis pipeline — a linear 6-node LangGraph state machine, three-tier evidence grounding, scoring, and the fail-closed persistence gate.
resource: docs/ARCHITECTURE.md
tags: [architecture, langgraph, pipeline]
timestamp: 2026-07-03T12:00:00Z
---

# Architecture

The compliance engine is a **linear LangGraph state machine** wrapped by a fail-closed engine. It grades documents against
retrieved evidence, ranked by trust, and refuses to persist a passing grade for anything it could not fully evaluate.

## Concepts

- [Compliance pipeline](compliance-pipeline.md) — the 6-node graph: `preprocess → dispatch → analysis → disclosure → scoring → refinement`.
- [Three-tier grounding](three-tier-grounding.md) — precedent > rule > novel, with consolidation (sweep, critic, dedup, suppression).
- [Scoring & fail-closed gate](scoring-and-fail-closed.md) — absolute-deduction scoring, critical cap, adaptive reliability, and the persistence gate.

## Related

- Implemented by the [Compliance engine](../services/compliance-engine.md) and graph nodes.
- Evidence supplied by the [RAG subsystem](../rag/index.md).
- Produces [violations](../data-model/violations.md) and [compliance checks](../data-model/compliance-checks.md).
