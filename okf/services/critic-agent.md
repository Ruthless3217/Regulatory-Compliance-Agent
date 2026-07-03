---
type: Service
title: Critic Agents
description: Two independent critics applied after tier-mapping — a deterministic evidence-grounding critic that drops fabricated quotes, and an LLM dual-model critic that downgrades low-confidence findings but never drops criticals.
resource: backend/app/services/agents/compliance/critic.py
tags: [service, critic, grounding, precision]
timestamp: 2026-07-03T12:00:00Z
---

# Critic Agents

Two critics run in order after the [three-tier grounding](../architecture/three-tier-grounding.md) findings are mapped to
violations.

## 1. Deterministic evidence-grounding critic

`verify_evidence_grounding` in `backend/app/services/agents/graph/nodes.py`, gated by `settings.critic_enabled` (default True).
Drops any violation whose `current_text` (normalized case/whitespace) is **not a verbatim substring** of the chunk — kills
fabricated evidence. Empty `current_text` (structural/novel) is kept.

## 2. LLM dual-model critic

`critique_violations` in `backend/app/services/agents/compliance/critic.py`, gated by `settings.llm_critic_enabled` (default
True). Runs on `critic_llm_service` (cheap model, e.g. gpt-5.4-nano). One prompt covers all surviving findings; returns a
`CritiqueResult`.

- Drops only when `not keep AND critic.confidence < 0.40 AND not critical`. **Criticals are never dropped.**
- Confidence adjustment: keepers → `min(primary, critic_conf)`; non-dropped non-critical rejects → `critic_conf * 0.5`.
- Stashes `critic_reason` / `critic_confidence` into `violation_metadata`.
- **Fails open:** any exception → original violations pass through unchanged.

## Related

- Configured via `CRITIC_LLM_*` — see [LLM & RAG config](../config/llm-and-rag.md).
- Produces refined [violations](../data-model/violations.md).
