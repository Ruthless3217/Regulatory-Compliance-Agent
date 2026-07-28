# UIN Duplication Report

**Date:** 2026-07-28 · Phase-4 deliverable: every duplicated UIN and the records
associated with it, plus resolution semantics.

44 fact cards → 41 distinct UINs. **Two collision groups, both ULIP. No card has
a blank/missing UIN**, and every filename prefix matches its JSON `uin`.

## Collisions

### `116L211V02` — Bajaj Life Supreme (2 records)
| File | product_name |
|---|---|
| `116L211V02-supreme-gold-leaflet.json` | Bajaj Life Supreme - Gold |
| `116L211V02-supreme-horizon-leaflet.json` | Bajaj Life Supreme - Horizon |

Structural flags identical; **guardrails diverge** (Horizon carries three
guardrails Gold lacks, incl. the zero-allocation-charge footnote rule and the
"7% guarantee applies to allocation-charge return, not investment performance"
avoidance, plus the group's only `must_state`).

### `116L214V01` — Bajaj Life Smart Wealth Goal VI (3 records)
| File | product_name | structural_flags.offers_guaranteed_benefits |
|---|---|---|
| `116L214V01-swg-child-wealth-brochure.json` | Smart Wealth Goal VI | **true** |
| `116L214V01-swg-joint-life-brochure.json` | …VI (Joint Life Wealth variant) | **true** |
| `116L214V01-swg-wealth.json` | …VI (Wealth Variant) | **false** ← previously the silent winner |

**Most serious finding:** the three variants genuinely disagree on
`offers_guaranteed_benefits` (the flag that gatekeeps "guaranteed" claims), and
last-file-wins meant the engine graded UIN 116L214V01 copy against the card
asserting `false`, chosen purely by filename sort order. Guardrail sets are
largely disjoint; two of three records were discarded on every lookup.

## Resolution semantics found (and what changed)

| Path | Before | Now |
|---|---|---|
| `FactCardService._load` | silent last-file-wins overwrite | **collision detected + WARNING logged at load; `collisions` property; `get_all(uin)`** |
| `FactCardService.get` | arbitrary single card | unchanged signature (compat) — but deterministic, documented, and loudly flagged |
| `resolve_products` fuzzy leg | emitted one entry **per card**, so variant cards burned the `product_match_max` budget (bug: `seen_uins` never updated) | **one entry per UIN** (best-scoring variant name) |
| `resolve_products` output | no ambiguity signal | **`ambiguous: true` + `candidates: [names]`** on every match whose UIN maps to >1 card |
| `DisclaimerRegistry` | duplicate id → load error, fail-closed | unchanged — this was already the correct pattern |

## Past Performance relevance

**None** — confirmed. `past_performance.json` has empty `product_lines`, so it
triggers purely on the keyword regex / LLM backstop; `ProductContext` (the only
carrier of UIN-derived state) never participates. The UIN collisions could not
have caused the false mismatch. Disclaimers that DO consume product context
(`ulip_risk`, `participating`) are exposed only if colliding cards disagree on
`regulatory_descriptor` — currently they do not, but the exposure is latent.

## Recommended follow-ups (need compliance sign-off — they change grading)

1. **Variant identity:** add `variant_code` to the fact-card schema and populate
   the existing-but-unused `ProductDocument.variant` column at ingest; resolve
   on `(uin, variant_code)`; DB uniqueness on that pair.
2. **Strictest-flag precedence:** when a document cites a collided UIN with no
   variant signal, grade against the union of guardrails with logical-OR on
   `offers_guaranteed_benefits` (gate stays closed) instead of any single card —
   or route to `needs_review` with `degraded="product_ambiguous"`.
3. Surface `ambiguous`/`candidates` (now emitted by the resolver) in the
   analysis prompt's PRODUCT block and in the review UI.
