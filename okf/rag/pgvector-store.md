---
type: Module
title: pgvector Vector Store
description: The default vector store — per-index upserts with embedding-identity stamping, and hybrid search fusing a cosine vector leg and a BM25 keyword leg via Reciprocal Rank Fusion.
resource: backend/app/services/rag/stores/pgvector_store.py
tags: [rag, pgvector, hybrid-search, rrf, bm25]
timestamp: 2026-07-03T12:00:00Z
---

# pgvector Vector Store

`backend/app/services/rag/stores/pgvector_store.py`. Implements the `VectorStore` [port](ports-and-factory.md) over PostgreSQL +
pgvector.

## Hybrid search

1. **Embedding-compat guard** (once per index per process): if stored rows carry a different `embedding_model` than the active
   embedder, raise `RAGDegraded` — fails closed against cross-embedding-space cosine corruption (NULL legacy models tolerated).
2. **Vector leg:** `1 - (embedding <=> qvec)` cosine, floored at `rag_min_cosine` (0.25), `LIMIT :recall`.
3. **BM25 leg:** `ts_rank_cd(search_tsv, plainto_tsquery('english', qtext))`, floored at `rag_min_ts_rank` (0.02); skipped when
   the query text is empty.
4. **Fusion:** [Reciprocal Rank Fusion](#rrf) with `k = rag_rrf_k` (60), take top-k.
5. **Hydration:** fetch top-k ids, return per-index columns.

Two floors prevent an off-topic query riding a lexical/semantic coincidence into the cited set.

## Upserts

Per-index `INSERT … ON CONFLICT (id) DO UPDATE`; every row stamped with `embedding_model` / `embedding_dim`; vectors formatted
as pgvector literals; UUID arrays validated element-by-element (injection guard).

## RRF

`rag/rrf.py`: `fused[id] += 1/(k + rank + 1)` across all result lists, sorted descending — cross-list fusion is purely
rank-based (score is only a within-list tiebreaker).

## Alternative backend

`stores/azure_search_store.py` implements the same protocol using Azure AI Search's native hybrid + semantic re-rank (no RRF).

## Related

- Populated by the [indexes](indexes.md); queried by the [retrievers](retrievers.md).
