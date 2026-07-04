---
type: Module
title: Azure Cohere Embedder
description: The default embedder — Cohere embed-v3-multilingual served on the Azure AI Foundry model-inference surface, producing 1024-dim vectors with document/query asymmetry.
resource: backend/app/services/rag/embedders/azure_cohere_embedder.py
tags: [rag, embeddings, cohere, azure-foundry]
timestamp: 2026-07-03T12:00:00Z
---

# Azure Cohere Embedder

`backend/app/services/rag/embedders/azure_cohere_embedder.py`. Wraps Cohere embed-v3 on Azure AI Foundry via the
`azure-ai-inference` async `EmbeddingsClient` (the Foundry `/models` surface, not the Azure OpenAI surface).

## Behavior

- `model` = deployment name `Cohere-embed-v3-multilingual`; `dim` = **1024** (derived from a deployment→dim map, not the shared
  setting, so a config mismatch can't silently corrupt cosine).
- Batches at 96 inputs; maps protocol `input_type` → Cohere `document` / `query`; re-sorts the response by item index to
  preserve order.
- TLS verify bypassed under `llm_insecure_tls` (for corporate SSL inspection). Failures raise `RAGEmbedFailed`.
- The deployment name doubles as the identity stamped on every indexed row, used by the pgvector store's embedding-compat guard.
- If `AZURE_INFERENCE_API_KEY` is empty it falls back to the first `LLM_API_KEY` (same Foundry resource).

## Config

`RAG_EMBEDDING_PROVIDER=azure_cohere`, `AZURE_INFERENCE_ENDPOINT`, `AZURE_COHERE_EMBED_DEPLOYMENT`, `RAG_EMBEDDING_DIM=1024`.
See [LLM & RAG config](../config/llm-and-rag.md).

## Related

- Selected by the [factory](ports-and-factory.md); consumed by the [pgvector store](pgvector-store.md).
