---
type: Section Index
title: RAG Subsystem
description: A pluggable, protocol-driven retrieval platform — swap embedders and vector stores by environment variable; hybrid vector + BM25 search fused with Reciprocal Rank Fusion over six indexes.
resource: backend/app/services/rag/
tags: [rag, retrieval, pgvector, embeddings]
timestamp: 2026-07-03T12:00:00Z
---

# RAG Subsystem

`backend/app/services/rag/`. Two `Protocol`s (`Embedder`, `VectorStore`) make backends swappable **by environment variable
only**. Supplies the evidence each grading tier needs.

## Concepts

- [Ports & factory](ports-and-factory.md) — the abstraction and config-driven provider selection.
- [pgvector store](pgvector-store.md) — hybrid vector + BM25 search fused with RRF; embedding-identity guard.
- [Azure Cohere embedder](azure-cohere-embedder.md) — Cohere embed-v3 on Azure AI Foundry (1024-dim).
- [Indexes](indexes.md) — the six vector indexes and what each stores.
- [Retrievers](retrievers.md) — precedent, rules, chat, similar-submissions, source-docs, product-docs.

## Key properties

- **Fail-closed vs fail-soft:** rules & precedents gate the grade; chunks/source/product-docs are advisory (return `[]`).
- **Embedding-identity guard:** every row stamps `embedding_model`/`embedding_dim`; a query-time mismatch raises `RAGDegraded`.
- **Two quality floors:** cosine (`rag_min_cosine=0.25`) + BM25 (`rag_min_ts_rank=0.02`) stop off-topic matches being cited.

## Related

- Configured by [LLM & RAG config](../config/llm-and-rag.md).
- Feeds the [dispatch node](../architecture/compliance-pipeline.md) and [chat](../api/chat.md).
