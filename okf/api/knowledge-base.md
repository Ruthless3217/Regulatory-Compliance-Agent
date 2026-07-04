---
type: API Router
title: Knowledge Base API
description: Ingest the precedent corpus, report corpus stats, hybrid-search precedents, and serve a 2-D embedding projection for the vector-space visualization.
resource: backend/app/api/routes/knowledge_base.py
tags: [api, knowledge-base, ingestion, projection, search]
timestamp: 2026-07-03T12:00:00Z
---

# Knowledge Base API

`backend/app/api/routes/knowledge_base.py`, prefix `/knowledge-base`.

| Method · Path | Purpose |
|---------------|---------|
| `POST /knowledge-base/ingest` | Ingest a precedent JSON folder; **path-traversal guard** confines `folder_path` to `kb_ingest_root`; preview mode |
| `GET /knowledge-base/stats` | Counts by category / severity / reviewer, distinct files |
| `GET /knowledge-base/search` | Hybrid search over `rag_compliance_examples`; 503 on embed/store failure |
| `GET /knowledge-base/projection` | 2-D UMAP/PCA projection of embeddings (thread executor) for the scatter viz |

## Projection

`VectorProjectionService` (`backend/app/services/vector_projection.py`) stacks embeddings from
`rag_compliance_examples` + `rag_rules` + `rag_source_docs` into one matrix, projects with UMAP (PCA fallback), caps at
`viz_points_per_index` (2000), and caches by method + collected-vector counts.

## Related

- Ingestion pipeline: [Knowledge base ingestion](../services/knowledge-base-ingestion.md).
- Rendered by the frontend [Knowledge Base UI](../frontend/routing.md).
