---
type: Database Table
title: Precedent Corpus (precedent_cases & rag_compliance_examples)
description: The reviewer-decision corpus that powers grading — v2 canonical precedent cases keyed by a canonical hash, and the v1 anchored-comment compliance examples.
resource: backend/app/models/
tags: [data-model, precedent, corpus, vectors]
timestamp: 2026-07-03T12:00:00Z
---

# Precedent Corpus

These vector tables hold **real past reviewer decisions**, the evidence behind the strongest
[grounding tier](../architecture/three-tier-grounding.md).

## `rag_compliance_examples` (v1)

Migration `0004`. Fields: document_id, title, task, section_label, chunk_text, `anchor_text`, reviewer_name, comment_text,
final_text_chunk, violation_category, severity, source_file. Embed text = `Document chunk: … / Compliance comment: …`.

## `precedent_cases` (v2, canonical)

Migration `0012`. Fields incl. `canonical_hash` (NOT NULL UNIQUE dedup key), highlighted_span, span_context, reviewer_comment,
reviewer_role, thread (JSONB), before/after_text, regulation_tags, `issue_type`, `why_rationale`, guideline_ref, severity,
product_category, `occurrence_count`, example_tickets. The embed text is an **issue-centric signature**
(`Issue / Why / Flagged text / Context`) so paraphrased violations still match.

## Vectorization

Both carry a pgvector `embedding` + a `search_tsv` tsvector for [hybrid search](../rag/pgvector-store.md).

## Related

- Written by [ingestion](../services/knowledge-base-ingestion.md); read by the [Precedent retriever](../rag/retrievers.md).
- Cited on [violations](violations.md) via the `cited_*` fields.
