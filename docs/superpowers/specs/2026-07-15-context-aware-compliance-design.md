# Context-Aware Compliance Analysis — Design

**Date:** 2026-07-15
**Status:** Approved. **ALL THREE WORKSTREAMS DONE** (TDD, 2026-07-15).
B (18 tests) · C (9 backend tests + fe typecheck) · A (18 backend tests + fe typecheck).
Migrations `0021` (grouping) + `0022` (document_type) unapplied until `alembic upgrade head`.
⚠️ `backend/tests/` is gitignored — the TDD tests live locally only, not in the repo.

Workstream A notes (as-built): `app/services/document_type.py` holds the vocabulary,
`requires_product_mandatory_elements` gate (strict/fail-closed default) and the LLM
`classify_document_type`. `preprocess_node` loads `submission.document_type` into
state metadata; `analysis_node` computes the gate and threads it into both prompt
builders + `suppress_nonproduct_mandatory_findings` (backstop that suppresses a
UIN/descriptor demand on editorial docs). Route `POST /submissions/classify` pre-fills
the picker; `new/page.tsx` auto-detects on paste-blur and the user confirms.

Workstream C notes (as-built): grouping is by **physical span overlap** within a
chunk (`group_chunk_violations`), replacing the delete-based `dedupe_chunk_violations`
in `grade_chunk` (which is now retained only for its test). Findings all kept; the
strongest **non-suppressed** member is `is_primary`. Scoring counts `is_primary` only.
Migration `0021` adds `group_id`/`is_primary` to `violations`. Frontend: `lib/violationGroups.ts`
(`groupViolations`/`primaryViolations`), review-tab cards show an "Also flagged as"
expander, the document highlight and the report collapse to primaries.
**Author:** Compliance-tooling team (Bajaj Life marketing/AI).

## Problem

Four reviewer-reported false-positive / usability problems in document analysis:

1. **Brand false positive.** The generic product category "term insurance" is flagged
   as a *brand* violation with the suggestion to use "Bajaj Life Insurance".
2. **Duplicate highlights.** The same phrase is reported multiple times with different
   violation kinds (precedent + rule + novel tiers all flag the same span).
3. **Wrong document assumptions.** A **blog article** is told it is missing a **UIN**
   (Unique Identification Number) — a product-brochure/ad obligation that does not
   apply to editorial content.
4. **Scenario tone.** Neutral, factual death-scenario phrasing ("passes away",
   "sudden death") is flagged as fear-based / negative tone.

## Root causes (verified against the code)

- **①④** — The seeded brand rules (`backend/scripts/seeds/bajaj_brand.yaml`) rule #7
  (naming; keyword list literally contains `"Bajaj Life Insurance"`) and rule #23
  (`avoid fear-based language`; keywords `die`, `deathbed`, `fear`, `stranded`) are
  pulled into the per-chunk prompt by hybrid retrieval whenever a chunk mentions
  "insurance" or a death scenario. Instruction **(C)** tells the model to "name the
  term to standardize", and the LLM over-applies the rule to legitimate
  insurance-domain language. The LLM's independent **novel** tier can produce the same
  false positive even with no rule retrieved. **No allowlist of acceptable
  domain terms/phrases exists anywhere.**
- **②** — `graph/nodes.py:dedupe_chunk_violations` collapses findings only when their
  quoted `current_text` is a **byte-for-byte** match, **within a single chunk**. It
  therefore misses overlapping-but-not-identical spans, cross-chunk repeats, and the
  disclosure tier (which bypasses it). There is **no document-level dedup**.
- **③** — There is **no semantic document-type concept** (only file format lives in
  `Submission.content_type`). Product grounding is triggered by a **fuzzy product-name
  match** (`product_resolver.py:57`); a blog that merely names a product loads that
  product's fact-card `must_state` guardrails (including "Plan UIN …") into the prompt
  (`preprocessing_service.py:676-724`), and the model dutifully flags the missing UIN.

## Unifying insight

①③④ are the same class: the system lacks **context-awareness** (domain meaning of
terms, and the type of document). ② is a mechanical dedup bug. The design is two
context workstreams plus one dedup workstream.

---

## Workstream B — Domain allowlist + prompt carve-out (fixes ① & ④) — SHIP FIRST

Smallest surface, no DB migration, no re-seed. Independent of the seeded rules.

### B1. Allowlist data file
`backend/data/compliance_allowlist.yaml`:
```yaml
# Acceptable insurance-domain language. Findings whose ENTIRE offending span is
# one of these must NOT be raised as brand-naming or tone violations.
generic_product_terms:
  - term insurance
  - term plan
  - term life insurance
  - endowment
  - endowment plan
  - ULIP
  - unit linked insurance plan
  - whole life
  - money-back
  - money back plan
  - savings plan
  - guaranteed savings plan
  - rider
  - sum assured
  - maturity benefit
  - death benefit
acceptable_scenario_phrases:
  - passes away
  - pass away
  - passed away
  - sudden death
  - untimely death
  - in the event of death
  - in case of death
  - death of the life assured
  - demise
  - unfortunate demise
  - loss of life
```
Loaded once (module-level cache) by a small helper, e.g.
`app/services/compliance_allowlist.py` exposing `load_allowlist()` and a pure
`is_allowlisted_span(text) -> bool` (normalized, exact-match against the union).

### B2. Prompt carve-out
`preprocessing_service.create_precedent_prompts` (and
`create_completeness_sweep_prompt`) gain an **ACCEPTABLE LANGUAGE** block, placed with
the mode instructions:
```
ACCEPTABLE LANGUAGE — the following are legitimate, standard insurance terms and
neutral descriptions of the insured event. NEVER flag any of these as a brand-naming
issue or a tone/fear issue, and NEVER suggest replacing a generic product category
with the company brand name:
  Generic product categories: term insurance, term plan, endowment, ULIP, ...
  Neutral death-scenario phrasing: passes away, sudden death, in the event of death, ...
Only flag such a phrase if the surrounding sentence itself is non-compliant for some
OTHER reason (e.g. an unsubstantiated guarantee) — never for the term/phrase alone.
```
Terms are read from the allowlist file so the block and the post-filter never drift.

### B3. Post-filter backstop
New pure helper in `graph/nodes.py`, applied in `grade_chunk` right after
`mark_structural_findings` (≈ line 1124):
```python
kept = suppress_allowlisted_findings(kept)
```
- Suppresses (routes to the existing "needs review" lane; does **not** silently drop)
  a finding **only when** its normalized `current_text` **equals** an allowlisted
  phrase **and** its category/grounding is brand- or tone-related
  (`category in {brand, tone}` or the description is a naming/tone rewrite).
- **Load-bearing safety property** (mirrors `mark_structural_findings`): fires only on
  a *whole-span* match, never when the allowlisted term is a substring of a longer
  offending claim. A real finding on "term insurance guarantees 25% returns" is kept.
- Never suppresses `critical`.

### B4. Config
`settings.allowlist_enabled: bool = True` (kill switch), mirroring `critic_enabled`.

### B5. Tests (TDD, mock the LLM)
- `is_allowlisted_span`: exact match, case/whitespace-insensitive; substring is NOT a
  match.
- `suppress_allowlisted_findings`: brand/tone whole-span term → suppressed; same term
  inside a larger claim → kept; critical → kept; non-brand/tone category → kept.
- Prompt builder includes the ACCEPTABLE LANGUAGE block and lists file terms.

---

## Workstream C — Overlap-aware grouping (fixes ②)

- Replace exact-match, per-chunk dedup with a **document-wide grouping** pass
  (run after `aggregate_grading`): findings whose spans overlap/contain (same chunk,
  offsets located by finding `current_text` inside the chunk text) or whose normalized
  text is identical (cross-chunk) share a `group_id`; the strongest by
  (severity → tier precedent>rule>novel) is the group **primary**.
- **Scoring counts each group once** (primary severity) so keeping the angles does not
  inflate the penalty. (Today's dedup deletes duplicates; we keep + link them and dedup
  only for the score.)
- **Migration:** add nullable `group_id (UUID)` and `is_primary (bool)` to `violations`.
- **API:** `_serialize_violation` emits `group_id`, `is_primary`.
- **Frontend:** violations list renders one expandable card per group ("also flagged
  as: …"); inline `<mark>` carries `group_id`; selecting shows the group. Empty-span
  (disclosure/document-level) findings stay ungrouped.

---

## Workstream A — Document-type awareness, hybrid (fixes ③)

- **Migration:** add nullable `document_type` to `submissions`
  (`product_marketing | blog_article | social | email | website | other`).
- **Classifier:** a cheap LLM call (critic profile) classifies pasted/uploaded text →
  suggested type; exposed as an endpoint the form calls to **pre-fill** the dropdown;
  user confirms/overrides before running.
- **Gate:** for **non-product** types the analysis prompt **omits the "MUST STATE"
  mandatory-element instruction** (UIN, regulatory descriptor). Everything else
  (misleading-claim checks, banned-claim guardrails, brand, SEBI, disclosures) still
  applies to every type. Fail-safe: unknown/unset type → strictest
  (`product_marketing`).
- **Frontend:** "Document type" dropdown in `new/page.tsx`, pre-filled with the
  auto-suggested value.

---

## Cross-cutting

- **TDD** for every workstream; **no live LLM/embeddings calls in tests** (mock).
- Two additive, nullable migrations (`document_type`; `group_id`/`is_primary`).
- **Order:** B (no migration, biggest visible win) → C → A.
- Fail-closed ethos preserved: gates default to the strictest interpretation; nothing
  is silently dropped (suppression routes to the review lane).
