# Cross-Chunk Document Context — Design

**Date:** 2026-06-15
**Status:** Approved (pending spec review)
**Author:** AI marketing team + Claude

## Problem

The compliance analysis grades each chunk **in isolation**. `create_precedent_prompts(content, precedents, rules)` (`backend/app/services/preprocessing_service.py:532`) receives only the focal chunk's text; `grade_chunk` (`backend/app/services/agents/graph/nodes.py:655`) runs chunks concurrently with no view of their neighbors.

Consequence: a requirement satisfied **elsewhere in the document** is invisible to the grader. The canonical failure is a claim in chunk 3 whose required disclaimer sits in chunk 5 or the document footer — the model raises a false "missing disclaimer." (Observed in the live run: near-duplicate "missing reference `[2]`/`[3]`" findings, symptomatic of no cross-chunk awareness.)

## Goal

Let each chunk be graded **against the surrounding document** so the grader can recognize that a required element (disclaimer, footnote, reference, substantiation) is already present elsewhere and **not** flag it as missing.

### Non-goals (YAGNI)

- **No cross-chunk contradiction detection** (a claim in ch.3 contradicting ch.5). Noisier capability; revisit only if evals demand it.
- **No change to how violations are anchored.** The focal chunk remains the only graded unit; every `current_text` still comes from the focal chunk.
- **No new database tables / chunk-edge graph.** Context is assembled at analysis time from the existing ordered chunk list. (Consistent with the June 2026 decision to reject a chunk-edge graph / Neo4j.)

## Design

### Behavior

Each chunk is still the only thing graded. The grading prompt gains a read-only **`DOCUMENT CONTEXT`** block: all chunks in order, the current one marked `>>> FOCAL CHUNK <<<`. The model uses the context for exactly one purpose — decide whether a requirement is **already satisfied elsewhere** — and is explicitly instructed:

- Grade **only** the FOCAL CHUNK.
- Quote `current_text` **only** from the FOCAL CHUNK; never from the context.
- Use DOCUMENT CONTEXT only to avoid raising a finding whose required remedy (disclaimer / footnote / reference / substantiation) is already present elsewhere in the document.

### Grounding interaction (why read-only is the safe choice)

`verify_evidence_grounding` (`nodes.py:226`) drops any finding whose `current_text` is not present in the **focal chunk**. It is **unchanged**. Because the prompt forbids quoting context text, every `current_text` continues to originate from the focal chunk, so there are **no false drops**. Grading the whole window (instead of read-only context) would break this invariant — hence read-only.

### Token guard — the footer is never dropped

`build_document_context(chunks, focal_index, budget)` assembles the block once per submission:

- **Full mode:** if the estimated token size of all chunks ≤ budget (default ~8000 tokens of context), include **every** chunk in order.
- **Windowed mode (over budget):** include
  - chunk 0 (document framing), plus
  - focal ± 2 chunks, plus
  - **the last 1–2 chunks, always** (footers / disclaimers / references live at the end).
  Elided ranges are shown as a marker (e.g. `[… chunks 6–22 omitted …]`) so the model knows context is partial.

This guarantees the disclaimer-in-footer case works even for long documents without multiplying tokens by chunk count.

### Configuration

- New flag `cross_chunk_context_enabled` (`config.py`), default **True**, so the feature can be A/B-tested in the precedent-replay eval (on vs off).
- New `cross_chunk_context_token_budget` (default 8000).

## Components & touchpoints

| Unit | File | Responsibility |
|---|---|---|
| `build_document_context()` | `preprocessing_service.py` (new helper) | Pure function: ordered chunk list + focal index + budget → context string (full or windowed, footer always kept). No I/O. |
| `create_precedent_prompts(..., document_context=None)` | `preprocessing_service.py:532` | Render the optional DOCUMENT CONTEXT block + the "grade only FOCAL / quote only FOCAL" instructions. Backward compatible: `None` ⇒ current behavior. |
| `grade_chunk` / dispatch | `graph/nodes.py:655` | Build context once per submission (all chunk texts in order), pass the focal-specific context into each `grade_chunk` and into the completeness sweep. |
| Settings | `config.py` | `cross_chunk_context_enabled`, `cross_chunk_context_token_budget`. |

`build_document_context` is a pure unit: input is the chunk list + focal index + budget, output is a string; testable without DB or LLM.

## Data flow

```
dispatch(submission)
  ├─ chunks = [c0, c1, ... cN]                       (already produced upstream)
  ├─ for each focal chunk ci (concurrent):
  │     ctx = build_document_context(chunks, i, budget)   # full or windowed, footer kept
  │     prompt = create_precedent_prompts(ci.text, precedents, rules, document_context=ctx)
  │     result = llm.generate_structured(prompt)           # grades ci only
  │     (completeness sweep also receives ctx)
  │     kept = verify_evidence_grounding(result, ci.text)  # UNCHANGED, checks focal only
  └─ dedupe / suppress / score                              (UNCHANGED)
```

## Error handling

- `build_document_context` with a single chunk ⇒ context is just that chunk marked FOCAL (degenerate, harmless).
- Empty / missing chunk text ⇒ skipped in context, never raises.
- Token estimator unavailable (tiktoken blocked by corporate firewall, see existing fallback in `preprocessing_service`) ⇒ fall back to a character-length heuristic for the budget check; never hard-fail.
- Flag off ⇒ `document_context=None` ⇒ exact current behavior (safe rollback).

## Testing (TDD)

Context builder (pure, no DB/LLM):
- full mode includes every chunk in order with the correct FOCAL marker;
- windowed mode triggers over budget and **always includes the last chunk(s)** even when the focal chunk is early;
- omitted-range marker rendered;
- budget boundary (just under / just over);
- single-chunk and empty-text degenerate cases.

Prompt:
- with `document_context` set, the prompt contains the FOCAL delimiter and the "do not grade context / quote only focal" instructions;
- with `document_context=None`, the prompt is byte-identical to today (no regression).

Grounding regression:
- a fabricated `current_text` (not in focal, present only in context) is still **dropped** by `verify_evidence_grounding`;
- a real focal `current_text` still **passes** when context is present.

## Rollout

1. Land behind `cross_chunk_context_enabled=True`.
2. Run precedent-replay eval **off vs on**; compare precision / false-positive rate on "missing disclaimer"-type findings and anchor recall.
3. Keep or default-off based on eval delta.
