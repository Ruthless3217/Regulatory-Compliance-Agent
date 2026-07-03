---
type: Design Pattern
title: Scoring, Reliability & the Fail-Closed Gate
description: Absolute-deduction scoring with a critical fail-cap and adaptive Beta-Binomial rule reliability, plus the engine's fail-closed persistence gate that refuses to grade unevaluated documents clean.
resource: backend/app/services/agents/compliance/scoring.py
tags: [architecture, scoring, reliability, fail-closed, grading]
timestamp: 2026-07-03T12:00:00Z
---

# Scoring, Reliability & the Fail-Closed Gate

## Scoring (absolute-deduction)

`ScoringService.calculate_scores` (`backend/app/services/agents/compliance/scoring.py`) — independent of category count:

```
overall = clamp(100 − Σ( severity_weight × rule_reliability θ × confidence ), 0, 100)
severity_weight:  critical=20  high=10  moderate=8  medium=5  low=2  informational=2
```

- Suppressed findings are excluded (they persist for review but never move the score).
- **Critical fail-cap:** any critical with confidence ≥ 0.50 caps `overall` at **70.0** → grade ≤ C, status `failed`.
- Grade: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F. Status: `passed` / `flagged` / `failed`.

## Adaptive rule reliability (HITL feedback loop)

`reliability.py` — each rule's penalty scales by a **Beta-Binomial posterior mean θ = α/(α+β)** learned from reviewer
accept/reject verdicts (`POST /compliance/violations/{id}/feedback`). NULL counts → θ = 1.0 (never under-penalize); floored at
0.30 (feedback discounts, never erases a rule). Default prior α=9, β=1. Reviewer *document* scores are stored for **eval only**
and never train the scorer.

## Fail-closed persistence gate

`ComplianceEngine.evaluate_persistability` (`compliance/engine.py`) blocks persistence when: no chunks (`no_content`), a failed
status, any `metadata["degraded"]` flag, or any failed chunk. Blocked runs set the [submission](../data-model/submissions.md)
to `needs_review` / `failed` and **return None** — never a passing grade. Clean runs persist the
[ComplianceCheck](../data-model/compliance-checks.md) + [Violations](../data-model/violations.md) + status flip **atomically**,
snapshotting `rule_version` so later rule edits can't rewrite history.

## Degraded labels

`rules_unavailable`, `no_content`, `knowledge_base_empty`, `rag_degraded`, `analysis_incomplete`, `disclosure_unavailable`.

## Related

- Consumes [violations](../data-model/violations.md) from [three-tier grounding](three-tier-grounding.md).
- Reliability updated by the [Rules API](../api/rules.md) feedback route.
