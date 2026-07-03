---
type: API Router
title: Chat API
description: SSE-streamed, RAG-grounded Q&A about a submission, plus violation-quote and compliant-rewrite helpers — with clamped input and per-call anti-injection fencing.
resource: backend/app/api/routes/chat.py
tags: [api, chat, sse, rag, anti-injection]
timestamp: 2026-07-03T12:00:00Z
---

# Chat API

`backend/app/api/routes/chat.py`, prefix `/chat`. All routes stream SSE (`token` / `done` / `error`) and are guarded by
`llm_rate_limit` + `llm_budget_guard`.

| Method · Path | Purpose |
|---------------|---------|
| `POST /chat` | Grounded Q&A — builds a system prompt from the full analysis report + rules/chunks/source/product passages + linked violations; cancels on client disconnect |
| `POST /chat/quote-violation` | Explain why a violation flags a passage (injects the verbatim regulator passage) |
| `POST /chat/suggest-rewrite` | Suggest a compliant rewrite of a flagged passage |

## Safety

- **Input clamping** — latest message + a tail of history are bounded (`chat_max_message_chars`, `chat_max_history_*`) before
  reaching the LLM.
- **Anti-injection fencing** — untrusted content wrapped in a per-call random `UNTRUSTED-<uuid>` fence.
- Runs on `chat_llm_service` (a separately-configurable provider — often Groq while analysis is Azure).

## Related

- Retrieval via the [Chat retriever](../rag/retrievers.md); streaming via the [LLM service](../services/llm-service.md).
- Consumed by the frontend [Chat UI](../frontend/routing.md).
