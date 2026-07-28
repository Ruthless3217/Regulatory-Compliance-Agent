# Verdict Explainability & Sensitivity Framework

**Date:** 2026-07-28 · companion to `ROOT_CAUSE_ANALYSIS.md`

This document describes how every disclaimer verdict is now explained, how
verdict sensitivity is measured, and what a "Jacobian" can and cannot mean for
an engine whose final tier is an API-hosted LLM.

---

## 1. Explanation payload

Every disclosure violation now carries, in `violation_metadata`
(`nodes._disclosure_finding_to_violation`):

| Field | Meaning |
|---|---|
| `verdict_provenance` | `deterministic_rule` (keyword/product-line trigger) or `hybrid` (LLM fired the obligation, deterministic matcher judged the wording). The wording judgement is **never** `llm_interpretation`. |
| `match_method` | `exact_normalized_match` \| `windowed_partial_ratio` \| `direct_ratio` |
| `match_reason` | `exact_match` \| `verbatim_window` \| `anchor_absent` \| `critical_token_lost` \| `partial_attempt` \| `fuzzy_floor_noise` \| `no_match` |
| `normalized_similarity` / `raw_similarity` | the score on normalised text (drives thresholds) and on raw text — both displayed, per Phase-6 |
| `evidence_span` | the best-aligned window of the normalised document — what the engine actually looked at |
| `token_overlap` | fraction of the required text's distinctive tokens present in that span |
| `critical_tokens_missing` | legally-critical words (not/no/may/guaranteed/risk/…) absent from the span |
| `decision_trace` | ordered human-readable steps that produced the verdict |
| `counterfactual` | the minimum valid change that flips the verdict (see §4) |
| `approved_wording`, `rule_source` | verbatim registry text + provenance of the rule |

The `required_disclosures` run summary carries the same evidence for
*compliant* disclaimers too, so "why was this NOT flagged" is equally answerable.

## 2. Decision order (deterministic before LLM)

```
1. exact normalised substring  → present            (no fuzzy metric involved)
2. anchors declared & absent   → altered/missing    (fail-closed statutory line)
3. sim ≥ present_threshold     → critical-token check → present | altered
4. altered ≤ sim < present     → token-overlap evidence gate → altered | missing
5. sim < altered_threshold     → missing
```

The LLM participates **only** in deciding which obligations a document carries
(paraphrase recall, e.g. "our fund grew 12% last year"). It never judges the
wording, and it can never un-flag a deterministic obligation. Two safeguards
added by this change-set:

- **Evidence gate (step 4).** `fuzz.partial_ratio` plateaus at ~0.45–0.55
  against unrelated prose. Previously that noise band was reported as "present
  but altered (similarity 0.50)" — the screenshot bug. Now "altered" requires
  the aligned span to share > 50 % of the required text's distinctive tokens;
  otherwise the verdict is an honest **missing**.
- **Critical-token check (step 3).** "Past performance **is** indicative of
  future performance" scores 0.96 — above the present threshold — while
  inverting the legal meaning. Any legally-critical token (not, no, may,
  guaranteed, risk, subject, past, future) present in the required wording but
  absent from the matched span downgrades present → altered. This closed a
  real pre-existing hole found during this work.

## 3. Sensitivity analysis (Jacobian-inspired, black-box)

`backend/scripts/verdict_sensitivity.py` perturbs **one variable at a time**
and records `(verdict, similarity)` movement — a finite-difference approximation
of ∂verdict/∂variable for the deterministic tier.

Measured influence matrix for the failing example (creative containing the
approved sentence), post-fix:

| Variable | Verdict impact | Confidence impact | Root-cause likelihood | Recommended action |
|---|---|---|---|---|
| disclaimer actually present/absent | **high** (flips) | −0.563 | ground truth — should flip | none (correct behaviour) |
| OCR letter-spacing artefacts | **high** (flips) | −0.375 | extraction quality | OCR/extraction hardening (done for empty-text pages; monitor) |
| exact-match path availability | low | 0.018 | none | windowed fallback covers it |
| markdown retained/stripped | none | 0.000 | none | — |
| reviewer comments appended | none | 0.000 | none | — |
| brand name old/new | none | 0.000 | none (disclaimer rules carry no brand filter) | — |
| UIN/product context | none for this rule (changes the *obligation set* only) | 0.000 | none | — |
| thresholds ±0.10 | none | 0.000 | none | — |
| chunk boundaries | none | 0.000 | none | — |
| context before/after removed | none | 0.000 | none | — |
| rule-wording punctuation (`**`, smart quotes) | none | 0.000 | UI-only (was the display bug) | data cleaned |
| similarity metric swap | none | 0.000 | none | — |

Interpretation: after the fix, the verdict responds **only** to the two
variables it should respond to — whether the wording is really there, and
whether extraction delivered it faithfully. Every cosmetic variable is flat.
Pre-fix, the dominant hidden variable was *extraction surface coverage*
(DOCX footers/text-boxes/tables, image-only PDF pages) — invisible in any
prompt-level ablation, which is why prompt tuning could never have fixed this.

Run it: `cd backend && PYTHONPATH=. python scripts/verdict_sensitivity.py
[--text yourfile.txt] [--json out.json]`.

## 4. Counterfactual methodology

Each violation's `counterfactual` states the **minimum valid change** that
flips the verdict, derived from `match_reason`:

- `fuzzy_floor_noise` / `no_match` → "add the approved wording — or, if it is
  visibly present in the artwork, fix extraction; no wording change required."
- `critical_token_lost` → names the exact word(s) to restore.
- `anchor_absent` → names the statutory anchor line.
- `partial_attempt` → replace the named span with the approved wording.

Counterfactuals never propose editing the approved rule, lowering a threshold,
or cosmetic tweaks that merely satisfy the matcher — a verdict may only change
in ways consistent with the registry rule and its effective version.

## 5. Limits of "Jacobian" analysis with API models

- The deterministic tier (registry, triggers, matcher) is fully analysable —
  the perturbation matrix above is exact and reproducible.
- The LLM tier (Opus/Azure endpoints) exposes no gradients or internals. We do
  **not** claim ∂output/∂input for it. What is available, and how it is bounded:
  - the backstop answers one narrow classification ("which obligation types
    apply") at temperature 0 with a structured schema — its influence on the
    final verdict is capped at *adding* obligations (it can never mark wording
    compliant/non-compliant);
  - A/B prompt ablations (with/without comments, with/without prior
    observations) are black-box finite differences: informative but stochastic;
    they belong in the eval harness with repeats, never inline in production;
  - a locally hosted differentiable reranker/classifier, if introduced later,
    could add true gradient attribution — that would be a separate,
    clearly-labeled channel and must not be conflated with API-model behaviour.
- Consequence of the architecture: because deterministic matching now precedes
  and bounds the LLM, the *unanalysable* surface area shrinks to obligation
  recall — where a false fire costs a review, never a false "wording wrong".

## 6. Provenance classification

| Provenance | Meaning | Where set |
|---|---|---|
| `deterministic_exact_match` | wording found verbatim after normalisation (`match_method=exact_normalized_match`; such disclaimers produce no violation) | matcher |
| `deterministic_rule` | obligation from keyword/product-line trigger; wording judged deterministically | disclosure_node |
| `hybrid` | obligation from LLM backstop; wording judged deterministically | disclosure_node |
| `llm_interpretation` | reserved for the precedent/rule grading lane (chunk-level findings), never disclosure verdicts | analysis nodes |
| `retrieval_similarity` | findings grounded in retrieved precedents/rules | analysis nodes |
| `manual_override` | reviewer action in the UI | review workflow |

The violation description string always ends with the trigger provenance
(`matched 'past performance'` or `llm:past_performance`), and
`violation_metadata.verdict_provenance` carries the machine-readable class.

## 7. Regression coverage

`backend/tests/test_disclosure_matching.py` (26 cases) and
`backend/tests/test_disclosure_extraction.py` (8 cases) pin: exact/markdown/
case/line-split/smart-quote presence; chunk-boundary spans; comment-adjacent
wording; fuzzy-floor noise → missing; dropped-"not" → altered; paraphrase →
altered; anchor fail-closed semantics; registry hygiene; explanation payload;
provenance classes; DOCX footer/header/table/text-box extraction; PDF OCR
fallback and its graceful absence. (Tests are local by repo policy —
`backend/tests` is gitignored.)
