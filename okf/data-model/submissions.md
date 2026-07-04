---
type: Database Table
title: submissions & content_chunks
description: The uploaded/pasted document under review and its section-aware token chunks.
resource: backend/app/models/submission.py
tags: [data-model, submissions, chunks]
timestamp: 2026-07-03T12:00:00Z
---

# submissions & content_chunks

## `submissions` (`models/submission.py`)

One row per document submitted for review.

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `title`, `content_type`, `original_content`, `file_path` | intake fields (text / upload / URL) |
| `submitted_by` | → `users.id` |
| `status` | `uploaded → preprocessing → preprocessed → analyzing → analyzed` \| `failed` \| `needs_review` |
| `approval_status` | pending / approved / rejected |
| `product_line`, `jurisdiction` | indexed — retrieval scoping |

Relationships: `compliance_checks` (cascade), `chunks` (ordered).

## `content_chunks` (`models/content_chunk.py`)

| Column | Notes |
|--------|-------|
| `submission_id` | → submissions (CASCADE) |
| `chunk_index`, `text`, `token_count` | produced by [ContextEngineeringService](../services/context-engineering-service.md) |
| `chunk_metadata` | JSONB |

Chunks are mirrored into [`rag_chunks`](rag-tables.md) for similarity/chat retrieval.

## Related

- Created by the [Submissions API](../api/index.md); graded by the [Compliance engine](../services/compliance-engine.md).
