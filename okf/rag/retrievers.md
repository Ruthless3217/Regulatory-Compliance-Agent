---
type: Reference
title: RAG Retrievers
description: Async, parallelized retrievers over the six indexes — precedent, rules, chat, similar-submissions, source-docs, product-docs — each with its own top-K and fail-closed vs fail-soft posture.
resource: backend/app/services/rag/retrievers/
tags: [rag, retrievers, top-k, retrieval]
timestamp: 2026-07-03T12:00:00Z
---

# RAG Retrievers

`backend/app/services/rag/retrievers/`. All async, parallelized via `asyncio.gather`, exposed as module-level singletons via
`get_*_retriever()`.

| Retriever | Index(es) | Default top-K | Notes |
|-----------|-----------|---------------|-------|
| `PrecedentRetriever` | `precedent_cases` (else `rag_compliance_examples`) | `pgvector_top_k` = 15 | Per-chunk hybrid search; drops thin comments and same-document precedents (eval-leakage guard). **Gates the grade.** |
| `RulesRetriever` | `rag_rules` | caller-supplied | Parallel `hybrid_search` per (chunk × category), filter `{category, is_active:True}`. **Gates the grade.** |
| `ChatRetriever` | rules + chunks + source + product | 5 / 3 / 2 / 3 | Four queries in parallel + in-process join to [violations](../data-model/violations.md); powers [chat](../api/chat.md). |
| `SimilarSubmissionsRetriever` | `rag_chunks` | `rag_top_k_similar` = 3 | Pure `vector_search`, filter `submission_status:analyzed`, ranks by max chunk score. |
| `SourceDocsRetriever` | `rag_source_docs` | 3 | `by_rule(rule_id)` SQL lookup on `derived_rule_ids`, or `semantic(query)` hybrid fallback. |
| `ProductDocsRetriever` | `rag_product_docs` | `rag_top_k_chat` = 5 | Fail-soft (returns `[]`, never gates a grade); optional uin/product/block filters. |

## Fail-closed vs fail-soft

Rules & precedents **gate** the compliance grade (a legitimately-empty precedent match → `knowledge_base_empty` → fail closed).
Chunks, source-docs, and product-docs are **advisory** and fail soft.

## Related

- Search executed by the [pgvector store](pgvector-store.md).
- Consumed by the [dispatch node](../architecture/compliance-pipeline.md) and the [Chat API](../api/chat.md).
