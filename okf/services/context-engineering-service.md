---
type: Service
title: ContextEngineeringService (Preprocessing)
description: Extracts text from uploaded documents, token-chunks it section-aware, and constructs the rule and precedent prompts, wrapping untrusted content in anti-injection fences.
resource: backend/app/services/preprocessing_service.py
tags: [service, preprocessing, chunking, prompts, anti-injection]
timestamp: 2026-07-03T12:00:00Z
---

# ContextEngineeringService

`backend/app/services/preprocessing_service.py`. Runs in the `preprocess` node of the
[pipeline](../architecture/compliance-pipeline.md).

## Responsibilities

- **Extraction** — multi-format document text extraction (PDF, DOCX, HTML, Markdown, text).
- **Chunking** — section-aware token chunking into [content_chunks](../data-model/submissions.md); each chunk carries an index,
  token count, and metadata.
- **Prompt construction** — builds the rule and precedent grading prompts consumed by the analysis node.
- **Anti-injection fencing** — untrusted document content is wrapped in per-call random `UNTRUSTED-<uuid>` fences so pasted copy
  can't hijack the grading instructions (mirrored in the [chat](../api/chat.md) route).

## Related

- Output chunks are mirrored into [`rag_chunks`](../rag/indexes.md) by the preprocess node (non-fatal).
- Also used by the [Rules API](../api/rules.md) `generate-from-document` extraction path.
