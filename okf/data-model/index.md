---
type: Section Index
title: Data Model
description: The PostgreSQL schema — submissions and chunks, rules, compliance checks and violations, the precedent corpus, and the RAG vector tables. Schema is owned by Alembic migrations.
resource: backend/app/models/
tags: [data-model, postgresql, schema, sqlalchemy, alembic]
timestamp: 2026-07-03T12:00:00Z
---

# Data Model

SQLAlchemy models under `backend/app/models/`. **Schema is owned by Alembic** (`backend/alembic/versions/`, head `0013`), never
`create_all`. UUID PKs, `timezone=True` timestamps, JSONB for flexible blobs.

## Core tables

- [Submissions](submissions.md) — uploaded content + its chunks.
- [Rules](rules.md) — versioned compliance rules with adaptive reliability weights.
- [Compliance checks](compliance-checks.md) — one grading result per persisted run.
- [Violations](violations.md) — findings with grounding provenance and a suppression lane.
- [Precedent corpus](precedent-cases.md) — `precedent_cases` (v2) + `rag_compliance_examples` (v1).
- [RAG tables](rag-tables.md) — the vector + BM25 indexes.
- [Document comparisons](document-comparisons.md) — persisted diffs for the Compare tool.

## Audit-defensibility

Rules are **versioned, not mutated** (edit → new row; old `superseded_by` new). Violations snapshot `rule_version` + citation
locators. Suppressed findings persist in a review lane rather than vanishing.

## Migration story (high level)

`0001` initial · `0002` rag tables · `0003–0005` violation confidence/citations · `0006–0007` KB tsvector + corpus purge ·
`0009` rule versioning + scoping · `0010` adaptive weights · `0011` product docs · `0012` precedent cases · `0013` document
comparisons.
