---
type: Service
title: Document Comparison Service
description: A standalone, LLM-free tool that diffs two document versions paragraph-by-paragraph with word-level highlighting and persists the result.
resource: backend/app/services/comparison_service.py
tags: [service, comparison, diff, standalone]
timestamp: 2026-07-03T12:00:00Z
---

# Document Comparison Service

`backend/app/services/comparison_service.py`. Powers the standalone **Compare** feature. **Pure text processing — no LLM.**

## How it works

- `extract_paragraphs` dispatches by content type: DOCX via python-docx (headings prefixed `## `), PDF via pdfplumber, else
  split on blank lines.
- `build_diff` aligns paragraph lists with `difflib.SequenceMatcher`; emits `equal` / `delete` / `insert` blocks directly;
  `replace` blocks with equal-length slices get per-paragraph **word-level** diffs, else degrade to delete+insert.
- Result persisted on [`document_comparisons.diff_result`](../data-model/document-comparisons.md) (JSONB); status
  `completed` / `failed`.

## Related

- Exposed by the [Comparisons API](../api/index.md) (`/comparisons`).
- Rendered by the frontend [Compare UI](../frontend/routing.md).
