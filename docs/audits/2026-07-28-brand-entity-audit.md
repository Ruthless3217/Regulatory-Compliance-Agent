# Brand/Entity Audit — "Bajaj Allianz Life" → "Bajaj Life" rename

**Date:** 2026-07-28 · investigation for the Past Performance false-mismatch work.

**Headline:** the rename was applied as a blanket textual find-and-replace of
"Allianz" across the repo. Retrieval and rule lookup are NOT brand-filtered, so
no records were hard-excluded — but the replace corrupted two runtime regexes,
three disclaimer registry texts, one seed rule and one fact card. Four
demonstrable defects were found and fixed; none of them caused the Past
Performance verdict (confirmed non-cause).

## Demonstrated defects (all fixed in this change-set)

1. **`brochure_parser.py` regexes gutted.** `_PRODUCT_PHRASE_RE` contained
   `Bajaj(?:\s+)?\s+Life` — the literal residue of `(?:\s+Allianz)?` with
   "Allianz" deleted. Result: `"Bajaj Allianz Life Smart Wealth Goal"` (legacy
   brochures) extracted **no product name**, and `_COMPANY_NAME_RE` failed to
   reject `"Bajaj Allianz Life Insurance Company Limited"` — so a legacy PDF's
   company running header could be ingested as the *product name*, which then
   poisons the exact-equality `product_name` retrieval filter in
   `rag_product_docs`. **Fixed:** both regexes restored to
   `(?:\s+Allianz)?` / `(?:allianz\s+)?` forms; pinned by
   `tests/test_brand_alias.py`.
2. **Disclaimer legal footers became a tautology.** `general_product.json`,
   `generic_non_product.json`, `ulip_risk.json` all read *"Bajaj Life Insurance
   Limited (Formerly known as Bajaj Life Insurance Limited)"*. Measured: this
   did NOT break matching (windowed similarity absorbs it, sim ≈ 0.98) — it is
   a content-correctness defect: the engine held up nonsense as the approved
   wording. **Fixed:** parenthetical restored to "Formerly known as Bajaj
   Allianz Life Insurance Company Limited"; guidelines doc regenerated.
3. **Seed brand rule self-nullifying.** `bajaj_brand.yaml` required the name to
   "appear as 'Bajaj Life Insurance' in full at first mention; 'Bajaj Life
   Insurance' is acceptable thereafter" — both clauses identical, severity
   high, LLM-judged: unenforceable and arbitrary. **Fixed:** full legal name at
   first mention ("Bajaj Life Insurance Limited"), short form thereafter,
   legacy-name guidance added. *Requires re-seed to reach the DB (see
   migrations in ROOT_CAUSE_ANALYSIS.md).*
4. **Fact card called the CURRENT name legacy.** `116B056V01-trad-fpr-leaflet.json`
   said "uses the legacy 'Bajaj Life Insurance' branding" — injected verbatim
   into analysis prompts as ground truth; the strongest mechanism for wrongly
   flagging correct current-brand creatives. **Fixed:** restored "legacy 'Bajaj
   Allianz Life Insurance' branding".

## Verified non-issues

- No retriever, trigger, or precedent path filters on company name — nothing is
  excluded by the rename at retrieval time.
- Disclaimer obligation triggers contain **no brand token** in any
  `keywords_regex`.
- No `effective_date` filtering exists anywhere yet: `Rule.effective_date` /
  `superseded_by` columns exist but are never queried; seeded rules carry NULL.
  (Follow-up recommendation below.)
- Fuzzy product-name scoring degrades (100 → 71–87) on old-brand creatives but
  stays above the 60 threshold — rank effect only, mitigated by the new
  brand-invariant parsing.

## Canonical mapping introduced

`backend/data/entities/balic.json` + `app/services/entity_registry.py`
(registry pattern, mirroring disclaimers/fact-cards): canonical name, short
name, CIN, aliases with status (`current`, `former`, `prohibited_customer_facing`
— BALIC/BLIL) and effective dates, `alias_regex` matching every era, and the
approved legal-footer form. Historical names are never rewritten in stored
records; they are mapped through this registry at match time.

## Follow-ups (not in this change-set — need product/compliance sign-off)

- Wire `rules_retriever` to honour `effective_date <= now` and represent the
  rename as a versioned rule row (columns already exist).
- Have `brochure_parser` / `product_resolver` build their brand patterns from
  `entity_registry.alias_regex("balic")` at import time (currently the corrected
  patterns are inline and covered by a sync test).
- Stale eval fixtures (`backend/eval/agentevalkit/dataset.jsonl`) still use the
  old name only.
