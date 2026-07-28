# Product Market-Segment Audit (Par / Term / Non-Par / ULIP)

**Date:** 2026-07-28 · declared segregation provided by the business, stored in
`backend/data/product_segments.json`; runtime derivation in
`rag/applicability.py::derive_segments` (par = `is_participating`,
ulip = `is_unit_linked`, term = `product_category`, non_par = neither flag on
individual savings/pension). Consistency pinned by
`backend/tests/test_product_segments.py`.

## Declared ↔ fact-card consistency: **clean**

Every declared product that has a fact card derives exactly its declared
segment from structural flags:

| Segment | Declared products with cards | Derivation check |
|---|---|---|
| Par | ACE (116N186V04), Elite Assure (116N127V04) | ✓ `is_participating=true` |
| Term | eTouch II (116N198V07), iSecure II (116N208V03), Diabetic Term Plan (116N183V01) | ✓ category `term` |
| Non-Par | GWG (116N200V10), AWG Platinum (116N188V09), Guaranteed Pension Goal II (116N187V09), Goal Suraksha (116N155V19), Saral Pension (116N169V16) | ✓ neither flag, savings/pension |
| ULIP | SWG (116L214V01), Fortune Gain (116L196V04), FWG (116L202V01), Supreme (116L211V02), MFP (116L207V02), IPG (116L205V01), LLG (116L203V01), Goal Assure (116L204V01) | ✓ `is_unit_linked=true` |

Version drift is tolerated by design (declared SWG **VII** / Goal Assure **IV**
vs cards SWG VI / Goal Assure IV etc. — matching is version-agnostic).

## Coverage gaps (declared, no fact card yet)

- Bajaj Life **ACE Advantage** (Par)
- Bajaj Life **FIG Plus** (Par)
- Bajaj Life **GBS III** (ULIP)
- Bajaj Life **Gain** (ULIP)

Creatives for these resolve to no product → scope unresolved → graded unscoped
with the `scope_unresolved` label until cards are added.

## Where the segregation now applies

- **Retrieval scope**: `build_scope` unions fact-card categories with derived
  segments, so rules/precedents can be tagged `par` / `non_par` / `term` /
  `ulip` and are accepted/rejected per the retrieval contract (C1-C7).
  The earlier heuristic (participating → savings_endowment) is replaced by the
  declared `par` segment.
- **Vocabulary**: `normalize_category` accepts `par`, `non_par`,
  `participating`, `non-participating`, etc.
- **Seed rules**: `bonus declaration history` rule tagged `product_line: par`
  (bonus history is a participating-plan concept). Conditional cross-segment
  rules ("guaranteed return … permitted only for traditional plans") stay
  global deliberately — they exist to catch misuse *outside* their segment.
- **Disclaimers**: already segment-aware via `product_lines: [par]/[ulip]`
  triggers driven by the same structural flags (no change needed).
