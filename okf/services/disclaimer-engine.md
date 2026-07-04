---
type: Service
title: Disclaimer Engine
description: Deterministic (plus optional LLM backstop) mandatory-disclosure checker — detects which disclaimers a document requires and verifies each is present verbatim, emitting document-level violations for missing/altered ones.
resource: backend/app/services/disclaimer/
tags: [service, disclaimer, disclosure, compliance, deterministic]
timestamp: 2026-07-03T12:00:00Z
---

# Disclaimer Engine

`backend/app/services/disclaimer/` — runs in the `disclosure` node of the
[pipeline](../architecture/compliance-pipeline.md), after analysis so it sees the whole document and the resolved product.
Registry files live in `backend/data/disclaimers/*.json` (one per mandated disclaimer).

## Registry (`registry.py`)

Loads disclaimer JSON into frozen dataclasses (id, type, text, anchors, severity, triggers, thresholds). **Fails closed:**
`loaded_ok` is True only if ≥1 file loaded and zero errors; any malformed file routes the whole run to `needs_review`.

## Triggers — which disclaimers are required (`triggers.py`)

- **Deterministic layer:** fires a disclaimer when its product-line (`ulip`/`par`/`any_product`/`non_product`) or a
  `keywords_regex` matches the document.
- **Precedence collapse:** specific tax disclaimers suppress the generic; a combined tax disclaimer supersedes its components.
- **LLM backstop** (`disclosure_llm_backstop_enabled`): may only *add* obligations; a source-gated exception lets one sanctioned
  combined-tax disclaimer suppress. LLM failure → deterministic-only + `recall_degraded`.

## Matcher — is it present verbatim (`matcher.py`)

Length-adaptive fuzzy match (`partial_ratio` vs `ratio`); a missing statutory **anchor line** → `altered` / `missing`. Missing
or altered required disclaimers become **document-level** violations (`chunk_id=None`, verbatim registry text as suggested fix).

## Config

Gated by `disclosure_check_enabled` (True); `disclaimers_dir` (default `data/disclaimers`).

## Related

- Emits [violations](../data-model/violations.md) that feed [scoring](../architecture/scoring-and-fail-closed.md).
