---
type: Section Index
title: Services
description: Backend services that power the compliance engine — orchestration entry point, LLM client, critic, chunking, corpus ingestion, disclaimer checking, and document comparison.
resource: backend/app/services/
tags: [services, backend]
timestamp: 2026-07-03T12:00:00Z
---

# Services

Backend services under `backend/app/services/`. The [compliance pipeline](../architecture/compliance-pipeline.md) is the spine;
these services implement its nodes and supporting features.

## Concepts

- [Compliance engine](compliance-engine.md) — entry point; runs the graph and persists results behind the fail-closed gate.
- [LLM service](llm-service.md) — async OpenAI/Azure-compatible client with structured output, retries, multi-key failover, budget.
- [Critic agent](critic-agent.md) — deterministic grounding critic + LLM dual-model critic.
- [Context engineering service](context-engineering-service.md) — document extraction, token chunking, prompt construction.
- [Knowledge base ingestion](knowledge-base-ingestion.md) — parses the reviewer-decision corpus into precedent vectors.
- [Disclaimer engine](disclaimer-engine.md) — deterministic mandatory-disclosure checker.
- [Comparison service](comparison-service.md) — standalone document diff (no LLM).

## Related

- Retrieval lives in the [RAG subsystem](../rag/index.md).
- Persistence targets the [data model](../data-model/index.md).
