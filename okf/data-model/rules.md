---
type: Database Table
title: rules
description: Versioned compliance rules with category/severity, scoping, points-deduction, and adaptive Beta-Binomial reliability weights learned from reviewer feedback.
resource: backend/app/models/rule.py
tags: [data-model, rules, versioning, reliability]
timestamp: 2026-07-03T12:00:00Z
---

# rules

`backend/app/models/rule.py`. Compliance rules synced to [`rag_rules`](rag-tables.md) for retrieval.

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `category`, `severity` | indexed |
| `rule_text`, `keywords` (JSONB), `pattern` | rule content |
| `is_active`, `rule_metadata` (JSONB) | |
| `version`, `effective_date`, `superseded_by` | **versioning** — edits create a new row; old row `superseded_by` new, deactivated |
| `product_line`, `jurisdiction` | scoping |
| `points_deduction` (Numeric, default -5.00) | overrides severity weight in [scoring](../architecture/scoring-and-fail-closed.md) |
| `reliability_alpha`, `reliability_beta` | **adaptive weights** (NULL = no feedback → θ=1.0) |
| `is_auto_generated`, `generation_source`, `confidence_score` | AI-extracted rules |
| `created_by` | → users |

## Versioning = audit trail

Because rules are versioned rather than mutated, a violation can snapshot `rule_version` and the full history is reconstructable.

## Related

- CRUD + generation via the [Rules API](../api/rules.md).
- Reliability θ tuned by reviewer feedback — see [scoring & reliability](../architecture/scoring-and-fail-closed.md).
