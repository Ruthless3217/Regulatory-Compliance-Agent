---
type: Section Index
title: Configuration & Deployment
description: How the system is configured (LLM providers, RAG providers, feature flags, budgets) and deployed (docker-compose topology, corporate CA handling, backups).
resource: backend/app/config.py
tags: [config, deployment, settings]
timestamp: 2026-07-03T12:00:00Z
---

# Configuration & Deployment

All backend settings live in a single pydantic `Settings` (`backend/app/config.py`, env-driven). Deployment is docker-compose.

## Concepts

- [LLM & RAG config](llm-and-rag.md) — providers, models, embeddings, feature flags, budgets.
- [Deployment](deployment.md) — container topology, CA/TLS handling, backups.

## Related

- Config powers the [LLM service](../services/llm-service.md) and the [RAG subsystem](../rag/index.md).
