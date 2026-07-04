---
type: Module
title: RAG Ports & Factory
description: Protocol contracts (Embedder, VectorStore) plus LRU-singleton factories that select concrete providers purely from configuration.
resource: backend/app/services/rag/ports.py
tags: [rag, ports, factory, abstraction, config]
timestamp: 2026-07-03T12:00:00Z
---

# RAG Ports & Factory

## Ports (`rag/ports.py`)

Three `Protocol`s decouple callers from backends:

- **`Embedder`** — `embed(texts, input_type)` where `input_type` is `search_document` (indexed content) vs `search_query`
  (queries); asymmetric models (Cohere v3) embed them differently.
- **`VectorStore`** — `upsert`, `delete`, `hybrid_search` (vector + BM25 + RRF), `vector_search` (pure cosine), `health`.
- Dataclasses `VectorDoc`, `SearchHit`; `IndexName` literal enumerates the six [indexes](indexes.md).

A single store instance serves all indexes; the index name is a parameter.

## Factory (`rag/factory.py`)

`get_embedder()` and `get_vector_store()` are `@lru_cache(maxsize=1)` singletons — callers never import concrete classes.

- Embedder from `RAG_EMBEDDING_PROVIDER` (default `azure_cohere`; only that is supported → else `RAGDegraded`).
- Store from `RAG_VECTOR_BACKEND` (`pgvector` default | `azure_search`).
- `reset_singletons()` clears caches for tests.

Swapping backends is a **one-env-var change** — this is the pgvector → Azure AI Search roadmap materialized.

## Errors (`rag/errors.py`)

`RAGDegraded` (recoverable, caller falls back) vs `RAGIndexingFailed` (upsert failed but the source DB write must NOT roll back);
`RAGEmbedFailed` subclasses `RAGDegraded`.

## Related

- Concrete backends: [pgvector store](pgvector-store.md), [Azure Cohere embedder](azure-cohere-embedder.md).
