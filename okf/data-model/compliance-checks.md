---
type: Database Table
title: compliance_checks
description: One grading result per persisted analysis run — overall score, grade, per-category scores, and an eval-only reviewer score that never trains the scorer.
resource: backend/app/models/compliance_check.py
tags: [data-model, scoring, grade, checks]
timestamp: 2026-07-03T12:00:00Z
---

# compliance_checks

`backend/app/models/compliance_check.py`. One row per **persisted** analysis run (degraded/fail-closed runs persist none — a
key fact for the `audit-trail/` cost-monitoring plan).

| Column | Notes |
|--------|-------|
| `id` | UUID PK |
| `submission_id` | → submissions (CASCADE, indexed) |
| `checked_at` | |
| `overall_score` (Float), `grade` (A–F) | from [scoring](../architecture/scoring-and-fail-closed.md) |
| `status` | passed / flagged / failed |
| `scores` (JSONB) | per-category subscores |
| `reviewer_score`, `reviewer_scored_at` | **held-out eval only — never trains the scorer** |

Relationships: `violations` (cascade).

## Related

- Produced by the [Compliance engine](../services/compliance-engine.md); read by the [Compliance API](../api/compliance.md) and
  the [dashboard](../api/index.md).
- Contains [violations](violations.md).
