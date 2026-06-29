# Product Fact-Cards

Curated, structured ground-truth for each Bajaj Life product, distilled from
its IRDAI-approved brochure. The compliance engine consults these for
**deterministic facts** (exact UIN, free-look days, participating/linked
status, what marketing may and may not claim) instead of relying solely on
fuzzy retrieval over `rag_product_docs`.

One JSON per product, named `<plan-uin>-<slug>.json`.

These are **hand-curated** (read from the brochure, not auto-extracted) so the
`compliance_guardrails` reflect real regulatory judgement, not parser output.

## Schema (v1.0)

| Field | Meaning |
|-------|---------|
| `schema_version` | Fact-card schema version. |
| `product_name` | Full marketing name incl. "Bajaj Life". |
| `uin` | Primary (plan) UIN. |
| `rider_uins` | UINs of riders referenced in the brochure. |
| `source_file` | Brochure filename this card was curated from. |
| `product_category` | One of: `term`, `ulip`, `savings_endowment`, `pension_annuity`, `health`, `group`, `rider`. |
| `regulatory_descriptor` | Verbatim IRDAI descriptor line (e.g. "A Non-Linked, Non-Participating, Individual Life Insurance Term Plan"). |
| `structural_flags` | Booleans that drive the highest-risk marketing checks (see below). |
| `variants` | Named plan variants/options, if any. |
| `key_benefits` | Headline benefits the product actually provides. |
| `riders_available` | Optional riders offered with the plan. |
| `eligibility` | Entry/maturity age, sum-assured, policy-term, premium-payment-term ranges. |
| `key_terms` | `free_look_period_days`, `grace_period`, `suicide_clause`, `tax_note`, etc. |
| `mandatory_disclosures_present` | Statutory disclosures found in the brochure (Section 41, GST, grievance, etc.). |
| `compliance_guardrails` | **The core value.** What marketing claims must be supported, must be avoided, and must be stated for this specific product. |
| `curation` | Provenance: who/what curated it and from where. |

### `structural_flags` (drive the riskiest checks)

| Flag | Why it matters for marketing review |
|------|-------------------------------------|
| `is_unit_linked` | ULIPs must carry market-risk disclosures; non-linked must not be sold as investments. |
| `is_participating` | Only par products may reference bonuses; non-par "guaranteed" language is bounded. |
| `market_risk_borne_by_policyholder` | Triggers the mandatory ULIP risk statement. |
| `offers_guaranteed_benefits` | Gatekeeps any "guaranteed" claim. |
| `offers_maturity_benefit` | Gatekeeps "get your money back / maturity" claims (often variant-specific). |
| `offers_loan` | Gatekeeps "take a loan against your policy" claims. |

### `compliance_guardrails`

```jsonc
{
  "claims_marketing_must_support": [ "variant/condition-bound truths a claim depends on" ],
  "claims_marketing_must_avoid":   [ "misrepresentations this product is prone to" ],
  "must_state":                    [ "UIN, descriptor, mandatory caveats" ]
}
```
