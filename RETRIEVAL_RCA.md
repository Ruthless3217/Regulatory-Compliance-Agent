# Retrieval RCA — cross-product contamination in compliance context

**Date:** 2026-07-28 · companion to `ROOT_CAUSE_ANALYSIS.md` (disclaimer verdict RCA).

**Headline:** product identity IS resolved before retrieval, and scope metadata
already EXISTS on the corpora — but no retrieval or post-retrieval step ever
consumes it. Rules and precedents are fetched by regulator-bucket + semantic
similarity across the entire corpus, so any product-scoped rule or precedent
can enter any product's context whenever wording is similar. The failure mode
is "metadata persisted but not used during retrieval" — in three separate
places — plus a degraded-mode fallback that maximises contamination.

## 1. RCA — the exact code path

The pipeline order (graph nodes) is:

```
librarian/preprocess:  resolve_products()          ← product/UIN known HERE
dispatch_node:         rules retrieval             ← product NOT passed
dispatch_node:         precedent retrieval         ← product NOT passed, filters=None
dispatch_node:         _resolve_product_grounding  ← uses product (fact cards, brochure passages)
analysis_node:         prompt construction         ← retrieved rules + precedents injected
```

`nodes.py` (librarian) computes `product_match` (UIN regex + fuzzy name against
fact cards) **before any retrieval**, stores it in run metadata — and then no
retrieval step reads it.

### Gate 1 — rules (`rag/retrievers/rules_retriever.py:83`)

```python
filters={"category": category, "is_active": True}
```

`category` is the regulator bucket (`irdai` / `sebi` / `brand` — from
`dispatch_node`: `categories = list(rules_serializable.keys())`). There is no
product dimension. The corpus is contaminating **by design**: `seeds/irdai.yaml`
mixes global rules with product-scoped ones in one bucket — ULIP risk-factor
statement (line 16), ULIP senior-citizen suitability (44), pension/annuity tax
disclosure (76), rider inclusions/exclusions (92), ULIP lock-in (96). A term
creative that mentions investment/market/tax vocabulary satisfies the BM25 leg
and cosine floor for these, and nothing downstream can reject them:
`_select_rules_for_chunk` (nodes.py:56) flattens and caps — no applicability
check.

**Dormant metadata:** `Rule.product_line` (`models/rule.py:37`) exists,
indexed, `nullable` — never set by `seed_rules.py`, never copied into
`rag_rules` by `rules_indexer.py`, never filtered on, absent from the
`_FILTER_WHITELIST["rag_rules"]` set (`pgvector_store.py:34`).

### Gate 2 — precedents (`rag/retrievers/precedent_retriever.py:151`)

```python
filters=None
```

Pure hybrid similarity across the ENTIRE precedent corpus. Yet:

- `precedent_cases.product_category` **exists** (migration `0012:40`), is
  **populated at ingest** (`precedent_ingestion.py:45-50` — heuristic hints:
  ULIP/Term/Pension/Child/Savings), is **upserted** (`pgvector_store.py:243`)
  and **returned** in hits (`_RETURN_COLUMNS["precedent_cases"]` line 73);
- it is NOT in `_FILTER_WHITELIST["precedent_cases"]` (line 41-43), so even a
  willing caller could not filter on it;
- `_hit_to_precedent` (precedent_retriever.py:24-43) **drops the field**, so
  post-retrieval validation downstream is impossible too;
- ingest taxonomy (`ULIP`, `Term`, `Savings`, `Pension`, `Child`) does not match
  the fact-card taxonomy (`ulip`, `term`, `savings_endowment`,
  `pension_annuity`, `rider`, `group`) — nothing normalises them;
- the legacy fallback corpus (`rag_compliance_examples`) has **no product
  metadata at all**.

Result: a ULIP surrender-charge precedent is retrievable and citable against a
term creative purely on shared phrases ("guaranteed benefits", "maturity
benefit", "tax benefit") — the LLM then writes a reviewer comment transplanting
ULIP requirements onto a term product. This is the exact Case A/C mechanism.

### Gate 3 — degraded-mode amplifier (`nodes.py:75-79`)

When per-chunk RAG fails (`rag_degraded`), `_select_rules_for_chunk` dumps the
**entire flat active-rule set** (up to the cap) into EVERY chunk's prompt —
ULIP + pension + rider rules included, for any product. Embedding outages
convert into maximum cross-product contamination rather than reduced recall.

### Non-gates (verified scoped or deterministic)

- **Product docs/passages** (`product_docs_retriever.py`): hard-filtered by
  `uin` — scoped (subject to the UIN-collision caveats in
  `docs/audits/2026-07-28-uin-duplication-report.md`; note `nodes.py` scopes to
  `uins[0]` only).
- **Fact cards**: deterministic UIN lookup, never embedded.
- **Disclaimers**: deterministic registry + trigger engine (no vector path).
  Coarse product scoping via `product_lines` (`ulip`/`par`/`any_product`/
  `non_product`) exists and works; `category_wise_disclaimer_bifurcation` does
  not exist in this codebase (confirmed again).
- **Reranking**: there is none (RRF fusion only) — nothing to audit; no
  reranker is "optimising semantic relevance over applicability" because no
  reranker exists.
- **Similarity floors**: cosine + ts_rank floors exist (audit C6) but they are
  *relevance* floors, not *applicability* floors — an on-topic-but-wrong-product
  chunk sails over them.

## 2. Failure trace (code-derived; DB currently empty — see note)

```
Uploaded document:   term-insurance creative ("Secure your family... tax benefits
                     under Sec 80C... affordable premiums, high cover")
→ identified product: Saral Jeevan (116N165V01), product_category=term
                     [resolve_products — CORRECT, and available in metadata]
→ generated query:   per-chunk hybrid query over rag_rules, categories=[irdai,sebi,brand]
→ filters:           {category: "irdai", is_active: true}   ← no product filter
→ candidate chunks:  global ad rules ✓, "Pension and annuity products: tax
                     treatment on maturity must be disclosed" (BM25: "tax",
                     "maturity"; cosine over shared insurance phrasing) ✗
→ incorrect chunk:   the pension/annuity tax rule (irdai.yaml:76)
→ why it survived:   only category+is_active are filterable; rule has no
                     product_line stamped; no post-retrieval applicability check;
                     _select_rules_for_chunk flattens & caps only
→ verdict effect:    rule enters the RULES block of the analysis prompt; the LLM
                     is instructed to emit rule_findings for listed rules it
                     deems violated → "tax treatment on maturity not disclosed"
                     flagged on a product with no maturity benefit
```

The same trace holds for precedents with `filters=None` (any corpus row is a
candidate for any creative). *Note:* the Docker volumes were reset (both DBs
empty), so this trace is derived from the retrieval code + seed corpus rather
than a live run; the regression tests in `backend/tests/test_retrieval_scope.py`
reproduce the mechanism executable-y with a stubbed store.

## 3. Vector/metadata audit

| Corpus | Scope metadata AT REST | Used at retrieval? |
|---|---|---|
| `rag_rules` (rules) | `category` (regulator bucket), `severity`, `is_active`. `Rule.product_line` exists in the ORM/DB but is never populated nor indexed into rag_rules | `category`, `is_active` only |
| `precedent_cases` (v2 precedents) | `product_category` (heuristic ingest), `issue_type`, `severity`, `regulation_tags`, `guideline_ref`, `ticket` | **none** (`filters=None`); `product_category` not whitelisted, dropped by the hit mapper |
| `rag_compliance_examples` (legacy precedents) | none (no product fields) | none |
| `rag_product_docs` (brochures) | `uin`, `product_name`, `block_type`, `section_path` | `uin` (hard filter) ✓ |
| fact cards (not embedded) | `uin`, `product_category`, `structural_flags`, `variants` | deterministic lookup ✓ (fed to prompt, NOT to retrieval scope) |
| disclaimers (not embedded) | `product_lines`, precedence, thresholds | deterministic triggers ✓ |
| version/effective-date | `Rule.effective_date`/`version`/`superseded_by` columns exist | never queried (pre-existing finding, brand audit) |

## 4. Recommended fix (smallest change that guarantees product-aware retrieval)

Not an architecture rewrite — a **scope + validate** layer using metadata that
already exists:

1. **`RetrievalScope`** built in `dispatch_node` from the already-computed
   `product_match` + fact cards (UINs, canonical product categories,
   `is_unit_linked`/`is_participating` structural flags). One new pure module:
   `app/services/rag/applicability.py`, with a canonical product-category
   vocabulary and alias normalisation (ingest's `ULIP`/`Savings`/`Pension` ↔
   fact cards' `ulip`/`savings_endowment`/`pension_annuity`).
2. **Post-retrieval applicability validation** (deterministic, Python-side —
   no store/SQL change, works for NULL-tagged rows):
   - rules: validate against `Rule.product_line` (now surfaced through the
     active-rules serialization);
   - precedents: validate against `product_category` (now mapped through the
     hit mapper);
   - verdicts: `accepted` / `rejected(category_conflict)` /
     `accepted(global_untagged)` / `accepted(scope_unresolved)`.
   Rejected chunks never reach prompt construction — including on the
   **degraded flat-rules path**.
3. **Populate the dormant scope columns**: `product_line` on the product-scoped
   seed rules (`ulip`, `pension_annuity`, `rider`, `savings_endowment`), read
   by `seed_rules.py`. Untagged rules remain global (fail-open by contract).
4. **Retrieval debugger**: every candidate logged with score, scope value,
   verdict + reason; stored in run metadata (`retrieval_debug`), with
   `used_in_final_verdict` derivable from `violation.rule_id` /
   `cited_precedent_id`.

Deliberately NOT done (bigger than needed, or policy calls): re-embedding, new
vector namespaces, cross-encoder reranker, SQL-side `IN-or-NULL` filters,
effective-date filtering (columns exist; separate change), UIN-variant
composite keys (see UIN report).

## 5. Retrieval contract

```
C1. A rule or precedent tagged with a product category that CONFLICTS with the
    resolved product categories MUST NOT enter context.
C2. Untagged (NULL/unknown-category) rules and precedents are GLOBAL: they MAY
    enter context for any product, and are labeled accepted(global_untagged).
C3. When product resolution finds NO product, scope is UNRESOLVED: nothing is
    rejected on product grounds; every hit is labeled accepted(scope_unresolved)
    so the reviewer can see grading ran unscoped.
C4. A ULIP-linked rider ACCEPTS rider-scoped AND ulip-scoped items (structural
    flags widen scope); a non-linked rider does not accept ulip-scoped items.
C5. Product-brochure passages and fact cards MUST be retrieved only by resolved
    UIN (already enforced); UIN collisions surface `ambiguous`, never silently
    pick (2026-07-28 UIN fix).
C6. Rejection reasons and scores are recorded per candidate; a rejected
    candidate MUST be absent from every prompt tier (normal AND degraded path).
C7. An unknown/unmappable category tag on a candidate is treated as GLOBAL
    (accept + label) — heuristic ingest tags must not overblock.
```

## 6/7. Regression tests + debugger

See `backend/tests/test_retrieval_scope.py` (Cases A — both directions —, B, C
encoded against the pure applicability layer and the retriever mappers) and
`backend/tests/test_retrieval_debugger.py` (persistence of the debug payload).

The debugger payload is durable: `analysis_runs.run_metadata` (JSONB, migration
`0022`) stores the whitelisted extract — retrieval scope, per-candidate
verdicts with reasons (rejections always kept, acceptances sampled),
`grounding_mix`, degradation flags, product matches. `used_in_final_verdict`
joins `violations.rule_id` / `cited_precedent_id` onto the candidate ids.

## 8. Files changed & operator steps

| File | Reason |
|---|---|
| `backend/app/services/rag/applicability.py` | NEW — scope build + deterministic applicability validation + debug records |
| `backend/app/services/agents/graph/nodes.py` | build scope in dispatch; validate chunk rules, fallback rules and precedents; `retrieval_debug` metadata |
| `backend/app/services/rag/retrievers/precedent_retriever.py` | surface `product_category` in the hit mapper (was dropped) |
| `backend/app/services/agents/compliance/engine.py` | `run_metadata_from_state` whitelisted extract, passed to close_run |
| `backend/app/services/run_tracker.py` | persist `run_metadata` on the analysis run |
| `backend/app/models/analysis_run.py` + `alembic/versions/0022_analysis_run_metadata.py` | JSONB `run_metadata` column |
| `backend/scripts/seeds/irdai.yaml` | `product_line` tags on the six single-family rules (ulip ×3, pension_annuity, rider, group) |
| `backend/scripts/seed_rules.py` | persist `product_line`; backfill it onto already-seeded rows on re-run |
| `backend/tests/test_retrieval_scope.py`, `test_retrieval_debugger.py` | regression tests (local — tests dir gitignored) |

Operator steps: `alembic upgrade head` (adds `analysis_runs.run_metadata`),
then `python -m scripts.seed_rules` (stamps `product_line` onto existing rule
rows; insert-idempotent). No re-embedding required — scope validation is
post-retrieval and reads the ORM/hit metadata, not the vectors.

---

## Postscript (2026-08-11) — contract inversion note

Contracts C2/C3/C7 above describe untagged/unresolvable candidates as GLOBAL
(fail-open). The shipped `rag/applicability.py` deliberately inverted this to
fail-closed: untagged or unmappable scope ⇒ `rejected: scope_metadata_missing`,
and an unresolved product rejects every tagged candidate. Consequence: active
rules with NULL `product_line` (~81 legacy rows predating migration 0009) are
excluded from every analysis until manually scoped. The rules-page banner now
states this; tag via the inline scope editor. Retrieval-side product
segregation (SQL filters both legs, migration 0036) additionally admits NULL
rows to the recall pool — the judge remains the strict gate.
