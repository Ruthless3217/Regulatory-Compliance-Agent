---
type: Database Table
title: violations
description: The findings of an analysis — description, severity, evidence text, grounding provenance (precedent/rule/novel), citation locators, and a suppressed review lane kept out of scoring.
resource: backend/app/models/violation.py
tags: [data-model, violations, grounding, provenance, suppression]
timestamp: 2026-07-03T12:00:00Z
---

# violations

`backend/app/models/violation.py`. The richest table — one row per compliance finding.

## Core

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `compliance_check_id` | → checks (CASCADE) |
| `rule_id` | → rules (SET NULL) |
| `category`, `severity`, `description`, `location` | finding |
| `current_text`, `suggested_fix` | flagged evidence + proposed rewrite |
| `auto_fixable`, `chunk_id`, `chunk_index` | |
| `confidence` (Float, default 0.85), `regulator_quote` | |
| `violation_metadata` (JSONB) | reviewer-voice tags; **`grounding: precedent \| rule \| novel`**, `action_type`, `evidence_needed` |

## Precedent-citation provenance

`cited_precedent_id`, `cited_document_id`, `cited_source_file`, `cited_anchor_text`, `cited_comment_verbatim`,
`cited_final_text`, `similarity_score` — carries the original reviewer decision onto the finding (audit).

## Rule citation locators + suppression

`cited_section`, `cited_page`, `cited_regulation_version`, `rule_version` (snapshot); `suppressed` (bool, indexed) +
`suppressed_reason` — the **review lane**: sub-confidence-floor and structural findings persist here but are excluded from
[scoring](../architecture/scoring-and-fail-closed.md).

## Related

- Produced by [three-tier grounding](../architecture/three-tier-grounding.md); grounded in [precedents](precedent-cases.md) and
  [rules](rules.md).
