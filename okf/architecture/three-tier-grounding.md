---
type: Design Pattern
title: Three-Tier Evidence Grounding
description: The analysis node emits findings into three lanes ranked by evidence strength — precedent > rule > novel — then consolidates via completeness sweep, critic, dedup, and structural suppression.
resource: backend/app/services/agents/graph/nodes.py
tags: [architecture, grounding, precedent, critic, analysis]
timestamp: 2026-07-03T12:00:00Z
---

# Three-Tier Evidence Grounding

The analysis node is three grounding engines feeding one consolidation stage. A single temperature-0 LLM call per chunk
(structured output `PrecedentCitationsResult`) emits findings into three ranked lanes at once. Implemented in
`backend/app/services/agents/graph/nodes.py`.

## The three tiers (ranked by trust)

1. **Precedent (Tier 1, strongest)** — "a reviewer flagged this exact thing before." Grounded in
   [`precedent_cases` / `rag_compliance_examples`](../data-model/precedent-cases.md). Carries the original reviewer comment,
   approved rewrite, and similarity score onto the [violation](../data-model/violations.md).
2. **Rule (Tier 2)** — "an active regulatory rule covers this." Grounded in [`rag_rules`](../rag/indexes.md) + regulator quote.
   Severity remapped conservatively (`high → moderate`) so rule findings don't inflate the critical count.
3. **Novel (Tier 3, weakest)** — expert judgment, allowed only when no precedent/rule applies. Must supply a `regulatory_basis`
   and clear `NOVEL_CONFIDENCE_FLOOR = 0.75`; below the floor it is **suppressed** (persisted for audit, kept out of the score).

## Per-chunk consolidation

After the tiers are mapped to violations:

1. **Corrective retry** — one retry if any citation index is out of range.
2. **Completeness sweep** — a second "what did you miss?" pass; new findings merged & deduped (`completeness_sweep_enabled`).
3. **Two critics** — see [Critic agent](../services/critic-agent.md): a deterministic grounding critic (drops findings whose
   `current_text` isn't a verbatim substring of the chunk) and an LLM dual-model critic (never drops criticals; fails open).
4. **Cross-tier dedup** — same-phrase findings collapse to the strongest by tier then severity
   (disclosure > product_fact > precedent > rule > novel).
5. **Structural suppression** — headings / brand lines are suppressed (not dropped) into an auditable review lane.

## Cross-chunk context

Each chunk is graded against a read-only view of the whole document (`cross_chunk_context_enabled`, budget 8000 tokens) so a
disclaimer present elsewhere (e.g. a footer) isn't falsely flagged as missing.

## Related

- Feeds [scoring](scoring-and-fail-closed.md).
- Uses the [LLM service](../services/llm-service.md) for the structured grade.
