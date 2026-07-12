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
| `diff_result` | JSONB — token-aligned blocks with word-level flags; `moved`/`move_id` on moved blocks |
| `status` | processing / completed / failed |
| `error_message` | |
| `render_result` | JSONB — pixel overlay (pages+boxes+changes); migration `0017` |
| `render_status` | processing / completed / failed / **skipped** (non-PDF) |
| `render_error` | |
| `created_by` | → users |

Index: `ix_document_comparisons_status`.

## `comparison_annotations` (migration `0020`)

`backend/app/models/comparison_annotation.py`. Reviewer notes & tags per change.

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `comparison_id` | → document_comparisons (ON DELETE CASCADE) |
| `change_id` | the viewer selection id (`r{n}` pixel / `b{index}` text) |
| `note` | nullable |
| `tags` | text[] |
| `created_by` | → users |
| `created_at` / `updated_at` | |

Unique `(comparison_id, change_id)`; index `ix_comparison_annotations_comparison`. A **re-run clears all rows** (change
ids are not stable across runs).

## Related

- Populated by the [Comparison service](../services/comparison-service.md) (no LLM) + `render_orchestrator` (PDF render).
- Rendered by the full-screen [Compare viewer](../frontend/routing.md).
