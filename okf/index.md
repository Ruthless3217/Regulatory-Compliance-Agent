---
type: Knowledge Bundle
title: Regulatory Compliance Agent — Knowledge Bundle
description: OKF v0.1 knowledge bundle for the Bajaj Allianz Life regulatory-compliance review system — its pipeline, services, RAG layer, data model, API, and frontend.
resource: ./
tags: [okf, index, compliance, langgraph, rag, fastapi, nextjs]
timestamp: 2026-07-03T12:00:00Z
---

# Regulatory Compliance Agent

An AI-powered regulatory-compliance reviewer for **Bajaj Allianz Life** marketing content. A user pastes or uploads copy; the
system grades it against IRDAI / SEBI / brand expectations by **imitating real past reviewer decisions ("precedents")** retrieved
from a vector store, and returns inline-highlighted violations, a scored report (0–100 + A–F grade), and a RAG-grounded chat.

> **Core principle:** the engine never grades from raw model opinion. Every finding is grounded in retrieved evidence — a past
> reviewer decision, an active rule, or high-confidence expert judgment. Anything it cannot evaluate **fails closed** to human
> review; nothing is ever silently graded clean.

## Stack

- **Frontend:** Next.js 15 (App Router) · React 19 · TypeScript · Tailwind · Radix/shadcn · Recharts.
- **Backend:** FastAPI · LangGraph orchestration · SQLAlchemy (async).
- **Data:** PostgreSQL + pgvector (hybrid vector + BM25 search) · Redis (LangGraph checkpointing, rate-limit, budget).
- **Models:** Azure OpenAI **gpt-5.4** (analysis) · gpt-5.4-nano (critic) · a separate chat LLM · Azure AI Foundry **Cohere
  embed-v3** (1024-dim). Multi-key Groq failover paths remain.

## How to navigate this bundle

Each section has its own `index.md`. Concepts link to each other with normal markdown links.

| Section | What's inside |
|---------|---------------|
| [Architecture](architecture/index.md) | The 6-node LangGraph pipeline, three-tier grounding, scoring & the fail-closed gate |
| [Services](services/index.md) | Backend services — engine, LLM client, critic, chunking, ingestion, disclaimer, comparison |
| [RAG](rag/index.md) | Pluggable retrieval — ports/factory, pgvector store, embedder, the six indexes, retrievers |
| [Data model](data-model/index.md) | PostgreSQL tables — submissions, rules, checks, violations, precedents, rag tables |
| [API](api/index.md) | FastAPI REST + SSE endpoints |
| [Frontend](frontend/index.md) | Next.js routing, API client, feature UIs |
| [Config](config/index.md) | LLM/RAG configuration and deployment topology |

## Authoritative sources in the repo

- `README.md` — quick start & feature overview (partly dated: describes 5 nodes/Gemini; the deployed system is 6 nodes/gpt-5.4).
- `docs/ARCHITECTURE.md` — the current, accurate system architecture (trust this over the README).
- `audit-trail/` — a separate design plan for auth, RBAC, and token/cost monitoring (proposal, not yet built).

## Change history

See [log.md](log.md).
