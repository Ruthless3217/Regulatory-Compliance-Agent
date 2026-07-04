---
type: Database Table
title: RAG Vector Tables
description: The pgvector + tsvector tables backing retrieval — rag_rules, rag_chunks, rag_source_docs, rag_product_docs — each with a VECTOR embedding column, a BM25 tsvector, and per-row embedding identity.
resource: backend/alembic/versions/0002_rag_tables.py
tags: [data-model, rag, pgvector, tsvector, embeddings]
timestamp: 2026-07-03T12:00:00Z
---

# RAG Vector Tables

The physical tables behind the [RAG indexes](../rag/indexes.md). Created by migrations `0002` (rules/chunks/source_docs), `0004`
(compliance_examples), `0011` (product docs), `0012` (precedent_cases).

## Common shape

- `embedding` — pgvector `VECTOR(dim)` (dim from `RAG_EMBEDDING_DIM`, 1024 for Cohere); ivfflat cosine index.
- `search_tsv` — `TSVECTOR` with BEFORE INSERT/UPDATE triggers for BM25 (`ts_rank_cd`).
- `embedding_model` / `embedding_dim` — per-row identity, checked by the store's embedding-compat guard.

## The tables

| Table | Source | Purpose |
|-------|--------|---------|
| `rag_rules` | [rules](rules.md) | rule retrieval per chunk × category |
| `rag_chunks` | [content_chunks](submissions.md) | similar-submissions + chat grounding |
| `rag_source_docs` | regulator PDFs | regulator passages; `derived_rule_ids` link back to rules |
| `rag_product_docs` | approved brochures | product grounding (fact cards + passages) |

`rag_compliance_examples` and `precedent_cases` are documented under [Precedent corpus](precedent-cases.md).

## Related

- Queried via [pgvector store](../rag/pgvector-store.md) + [retrievers](../rag/retrievers.md).
- Re-embed after a model change with `python -m scripts.rag_backfill`.
