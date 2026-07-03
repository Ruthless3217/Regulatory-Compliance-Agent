---
type: API Router
title: Rules API
description: Versioned CRUD for compliance rules plus AI rule extraction from regulator documents; content edits create a new rule version rather than mutating in place.
resource: backend/app/api/routes/rules.py
tags: [api, rules, crud, versioning, generation]
timestamp: 2026-07-03T12:00:00Z
---

# Rules API

`backend/app/api/routes/rules.py`, prefix `/rules`.

| Method · Path | Purpose |
|---------------|---------|
| `POST /rules` | Create a rule + best-effort RAG upsert |
| `GET /rules` | List (filter by category / is_active) |
| `GET /rules/{id}` | Fetch one |
| `PATCH /rules/{id}` | **Versioned update** — a content change (severity/text) creates a NEW row (version+1), supersedes + deactivates the old, links `superseded_by`; a pure activate/deactivate mutates in place |
| `DELETE /rules/{id}` | Delete + RAG delete |
| `POST /rules/generate-from-document` | AI rule extraction from an uploaded doc/text (guarded by `llm_rate_limit`; whitelisted extensions; 200k-char cap) |

## Versioned edits

The PATCH semantics are what make [rules](../data-model/rules.md) an audit trail: history is preserved, and
[violations](../data-model/violations.md) snapshot `rule_version`. *(The `audit-trail/` plan hooks this endpoint to also record
who changed what.)*

## Related

- Rules retrieved by the [Rules retriever](../rag/retrievers.md); generation uses the
  [ContextEngineeringService](../services/context-engineering-service.md) + `RuleGeneratorService`.
