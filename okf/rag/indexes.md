---
type: Reference
title: The Six Vector Indexes
description: The six pgvector indexes and what each stores — submission chunks, rules, regulator passages, v1 precedents, v2 canonical precedents, and product brochures.
resource: backend/app/services/rag/indexers/
tags: [rag, indexes, embeddings, schema]
timestamp: 2026-07-03T12:00:00Z
---

# The Six Vector Indexes

Each index has a dedicated indexer under `backend/app/services/rag/indexers/`. All follow `embed → VectorDoc → store.upsert` and
re-raise `RAGDegraded` as `RAGIndexingFailed` so the source DB write is never rolled back.

| Index | Indexer | Stores |
|-------|---------|--------|
| `rag_chunks` | `chunks_indexer.py` | [Submission](../data-model/submissions.md) content chunks mirrored from `content_chunks`; carries submission status (`analyzing`→`analyzed`). |
| `rag_rules` | `rules_indexer.py` | [Rules](../data-model/rules.md) synced from the `rules` table; embed text prepends `[category] [severity]` so retrieval respects them. |
| `rag_source_docs` | `source_docs_indexer.py` | Regulator PDF passages (~1200-token chunks); carries `derived_rule_ids` (backfilled after LLM rule extraction). |
| `rag_compliance_examples` | `compliance_examples_indexer.py` | **v1** precedent corpus (draft chunk + reviewer comment + anchor + final rewrite). |
| `precedent_cases` | `precedent_indexer.py` | **v2** canonical precedents keyed by `canonical_hash`; embeds an issue-centric signature so paraphrased violations still match. |
| `rag_product_docs` | `product_docs_indexer.py` | Approved-brochure reference corpus; sections embedded with heading path, tables embedded only as deterministic summaries (raw rows never embedded). |

## Notes

- The [`README.md`](../../README.md) and `docs/ARCHITECTURE.md` sometimes refer to "four indexes" — the core four are
  `rag_chunks`, `rag_rules`, `rag_source_docs`, `rag_compliance_examples`; `precedent_cases` (v2) and `rag_product_docs` were
  added later.
- Migrations `0002` (rag tables), `0004` (compliance examples), `0011` (product docs), `0012` (precedent cases).

## Related

- Backed by [pgvector](pgvector-store.md); queried by [retrievers](retrievers.md).
- Populated by [ingestion](../services/knowledge-base-ingestion.md).
