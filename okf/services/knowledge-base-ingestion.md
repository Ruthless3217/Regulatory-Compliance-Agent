---
type: Service
title: Knowledge Base & Precedent Ingestion
description: Parses the reviewer-decision corpus into precedent vectors — v1 anchored comments into rag_compliance_examples, v2 LLM-enriched canonical cases into precedent_cases.
resource: backend/app/services/knowledge_base_ingestion.py
tags: [service, ingestion, precedent, corpus, embedding]
timestamp: 2026-07-03T12:00:00Z
---

# Knowledge Base & Precedent Ingestion

Two generations of precedent ingestion turn a corpus of past reviewer decisions into searchable vectors that power the
[precedent tier](../architecture/three-tier-grounding.md).

## v1 — `knowledge_base_ingestion.py` → `rag_compliance_examples`

- Parses `[Reviewer]: comment (Context: anchor…)` files; malformed lines logged, never silently dropped.
- **Fuzzy anchoring:** exact substring → score 100; else `rapidfuzz.partial_ratio`, accepted only if ≥ `kb_min_fuzzy_score` (60).
- Chunking `kb_chunk_size=500` / overlap 50; classifies category (keyword map) + severity (by reviewer org: Legal/Compliance →
  critical, Marketing → moderate, else informational).
- Idempotent via `uuid5` over `(source_file|chunk|comment|anchor|reviewer)`.

## v2 — `precedent_ingestion.py` → `precedent_cases`

- Filters substantive reviewer comments → LLM-**enriches** (issue_type / why_rationale / severity, disk-cached because the
  canonical hash depends on the LLM output) → **dedups** by SHA-256 `canonical_hash` (same issue rolls up `occurrence_count`) →
  embeds an issue-centric *signature* so paraphrased violations still match.
- Enrichment runs concurrently (`Semaphore(20)`) with a safe fallback on failure. See `precedent/{dedup,enrichment,remediation}.py`.

## Run it

`python -m scripts.ingest_knowledge_base` (v1) and `python -m scripts.ingest_precedent_cases` (v2), inside the backend
container.

## Operational note

If the corpus is empty, analysis produces **no** violations and the run carries `degraded: "knowledge_base_empty"` (fail-closed).

## Related

- Writes to [precedent_cases / rag_compliance_examples](../data-model/precedent-cases.md).
- Retrieved by the [Precedent retriever](../rag/retrievers.md).
