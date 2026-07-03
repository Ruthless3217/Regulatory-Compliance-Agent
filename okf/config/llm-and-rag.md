---
type: Reference
title: LLM & RAG Configuration
description: The environment-driven settings that select LLM providers (main/chat/critic), embeddings, RAG tuning, feature flags, and cost/rate budgets.
resource: backend/app/config.py
tags: [config, llm, rag, feature-flags, budget]
timestamp: 2026-07-03T12:00:00Z
---

# LLM & RAG Configuration

`backend/app/config.py`. Highlights (defaults; env overrides):

## LLM providers (three profiles)

- **Main** (analysis) — `LLM_PROVIDER` (`azure` in prod), `LLM_MODEL` (deployment name, e.g. gpt-5.4), `LLM_API_KEY`
  (comma-list = multi-key failover), `LLM_USE_MAX_COMPLETION_TOKENS`, `LLM_SUPPORTS_TEMPERATURE`, `LLM_REASONING_EFFORT`.
- **Chat** — `CHAT_LLM_*`; any left empty inherits main (used to keep chat on Groq while analysis is Azure).
- **Critic** — `CRITIC_LLM_*` (e.g. gpt-5.4-nano); blank bool fields map to "inherit main" via a validator.

## Embeddings / RAG

`RAG_EMBEDDING_PROVIDER=azure_cohere`, `RAG_VECTOR_BACKEND=pgvector|azure_search`, `RAG_EMBEDDING_DIM=1024`; top-k
(`rag_top_k_analysis=8`, `pgvector_top_k=15`, chat=5, similar=3); floors `rag_min_cosine=0.25`, `rag_min_ts_rank=0.02`;
`rag_rrf_k=60`, `rag_recall_pool=30`. See [ports & factory](../rag/ports-and-factory.md).

## Feature flags

`critic_enabled`, `llm_critic_enabled`, `completeness_sweep_enabled`, `cross_chunk_context_enabled` (budget 8000),
`product_grounding_enabled`, `disclosure_check_enabled`, `disclosure_llm_backstop_enabled`, `grade_concurrency=2`.

## Budgets & limits

`llm_global_daily_token_budget` / `llm_daily_token_budget` (Redis day-keyed), `llm_context_window=128000`,
`http_rate_limit_per_min=30`, `trust_forwarded_for` (proxy IP trust), chat input caps, `max_upload_size=50MB`, `kb_ingest_root`
(ingest confinement).

## Gotchas

- `README.md` documents Gemini defaults; the deployed config is Azure gpt-5.4 (see `docker-compose.yml`).
- A few referenced settings (`llm_is_azure`) are not defined in `Settings` — latent, guarded. See [LLM service](../services/llm-service.md).

## Related

- Consumed by every service; see [Deployment](deployment.md) for the env wiring.
