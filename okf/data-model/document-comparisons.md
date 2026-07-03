---
type: Database Table
title: document_comparisons
description: Persisted results of the standalone document-diff tool — the two versions, their JSONB diff, and a processing status.
resource: backend/app/models/document_comparison.py
tags: [data-model, comparison, diff]
timestamp: 2026-07-03T12:00:00Z
---

# document_comparisons

`backend/app/models/document_comparison.py` (migration `0013`, current head).

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `title` | |
| `old_content_type` / `new_content_type` | |
| `old_file_path` / `new_file_path` | |
| `old_original_content` / `new_original_content` | |
| `diff_result` | JSONB — paragraph-aligned blocks with word-level flags |
| `status` | processing / completed / failed |
| `error_message` | |
| `created_by` | → users |

Index: `ix_document_comparisons_status`.

## Related

- Populated by the [Comparison service](../services/comparison-service.md) (no LLM).
- Rendered by the frontend [Compare UI](../frontend/routing.md).
