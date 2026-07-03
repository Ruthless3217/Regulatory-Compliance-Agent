---
type: Service
title: LLMService
description: Async OpenAI/Azure-compatible LLM client with schema-validated structured output, correction retries, multi-key failover, context-budget enforcement, and fail-closed error handling.
resource: backend/app/services/llm_service.py
tags: [service, llm, azure, groq, structured-output, failover]
timestamp: 2026-07-03T12:00:00Z
---

# LLMService

`backend/app/services/llm_service.py`. Three profile singletons — `llm_service` (main, Azure gpt-5.4), `chat_llm_service`
(chat), `critic_llm_service` (gpt-5.4-nano). Each field resolves with inherit-from-main fallback; one client is built per API
key for failover.

## Structured output (`generate_structured_response`)

- Appends the JSON schema from `output_model.model_json_schema()`, enforces a **context budget** (fails closed if
  prompt + reserved output exceeds the model window — prevents silent tail truncation).
- **Multi-key driver loop:** for Groq, acquires a per-key token budget; on `DailyLimitApproaching` rotates keys; reserves a
  global wallet budget (`GlobalTokenBudget`, Redis day-keyed).
- Per-key attempt loop (`max_retries=3`): calls with `response_format={"type":"json_object"}`; **fails closed on
  `finish_reason == "length"`**; validates via `model_validate_json`. Only a `ValidationError`/`JSONDecodeError` is retryable
  (re-prompts the model with the error). HTTP 429 → rotate keys.
- **Design intent: fail closed.** A compliance system must never fabricate an empty/clean result — all keys exhausted →
  `LLMUnavailableError`.

## Streaming & plain text

`generate_response` (plain) and `stream_response` (SSE, used by [chat](../api/chat.md)) share the failover pattern; streaming can
only fail over before the first token and requests `stream_options={"include_usage": True}` for real token usage.

## Token accounting

`_estimate_tokens` (tiktoken `cl100k_base`, else 4 chars/token); usage reserved-before / reconciled-after against the global
budget and per-key Groq limiter; pushed to LangSmith. *(This usage object is the hook point proposed by the
`audit-trail/` token-monitoring plan.)*

## Gotchas

- References `settings.llm_is_azure` in one health-check exception branch, which is not defined in `Settings` — a latent
  `AttributeError` on that path only.
- Imports `record_tokens` from `llm_budget` (doesn't exist) — a silent no-op via try/except.

## Related

- Configured by [LLM & RAG config](../config/llm-and-rag.md).
- Used by [three-tier grounding](../architecture/three-tier-grounding.md) and the [Critic agent](critic-agent.md).
