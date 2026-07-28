# Root Cause Analysis — "Past Performance" false disclaimer mismatch

**Date:** 2026-07-28
**Symptom (screenshot):** creative visibly contains the approved wording
*"Past performance is not indicative of future performance."* yet the engine reports:

> Disclaimer present but altered/incomplete: Past Performance (similarity 0.50).
> Replace with the approved wording. (llm:past_performance)

**Verdict of this analysis: the matching engine is NOT at fault. The sentence
never reached the matcher — it was silently dropped at the text-extraction
stage. Every downstream stage then behaved per spec but compounded the error
into a misleading "present but altered" message.**

---

## 1. The verdict pipeline (as traced in code)

```
Creative/file input
→ text extraction            preprocessing_service._extract_from_file      ← ❌ FAILING STAGE
→ chunk creation             preprocessing_service._chunk_text
→ document reassembly        nodes.disclosure_node: "\n".join(chunk texts)
→ obligation triggers        disclaimer/triggers.deterministic_triggers    (regex, IGNORECASE, full doc)
→ LLM obligation backstop    nodes._disclosure_llm_call (windowed, unioned)
→ verbatim matching          disclaimer/matcher.classify (rapidfuzz, normalized)
→ verdict construction       nodes._disclosure_finding_to_violation
→ UI rendering               violation.description + suggested_fix
```

There is **no** `category_wise_disclaimer_bifurcation` (or the `biforcation`
misspelling) anywhere in the codebase — that hypothesis is retired. Disclaimer
selection is the registry + trigger mechanism above.

## 2. Stage-by-stage trace for the failing example

Reproduced 2026-07-28 on the live codebase (`scratchpad/repro_past_performance.py`),
registry loaded from `backend/data/disclaimers/` (10 disclaimers, `loaded_ok=True`).

```json
{
  "creative_original_text": "…body copy with return claims… [footer/graphic] Past performance is not indicative of future performance.",
  "creative_normalised_text": "(normalisation never sees the footer — it was dropped one stage earlier)",
  "comments_detected": [],
  "text_sent_to_model": "body copy WITHOUT the disclaimer sentence",
  "brand_detected": "n/a — brand mapping plays no role in this verdict path",
  "canonical_legal_entity": "n/a",
  "category_detected": "mandatory disclosure",
  "product_detected": "none required for this rule (empty product_lines)",
  "variant_detected": "n/a",
  "uin_detected": "n/a",
  "candidate_rules": ["past_performance (registry, precedence 80)"],
  "selected_rule_id": "past_performance",
  "selected_rule_version": "file version in backend/data/disclaimers/past_performance.json (source: 'Copy of Disclaimers.xlsx')",
  "approved_wording": "**Past performance is not indicative of future performance.",
  "similarity_method": "rapidfuzz fuzz.partial_ratio over punctuation-stripped, lowercased, whitespace-collapsed text",
  "similarity_score": 0.50,
  "chunk_id": "document-level (chunk_id=None)",
  "prompt_sent_to_model": "disclosure backstop: 'which obligations apply… (e.g. our fund grew 12% last year implies a past-performance obligation)'",
  "raw_model_response": "{\"obligations\": [\"past_performance\"]}  — obligation classification only; the LLM never judges presence/absence of the wording",
  "final_verdict": "altered (0.45 ≤ 0.50 < 0.85) → 'Disclaimer present but altered/incomplete'",
  "verdict_source": "deterministic matcher (rapidfuzz); LLM only supplied the obligation trigger (provenance 'llm:past_performance')"
}
```

**First stage at which correct information becomes incorrect: text extraction.**

## 3. Evidence

### 3.1 The matching core is correct when it sees the sentence

Controlled runs through the real `DisclaimerRegistry` + `deterministic_triggers`
+ `matcher.classify` (no mocks):

| Case | Trigger | Status | Similarity |
|---|---|---|---|
| Exact sentence only | `matched 'Past performance'` (deterministic) | **present** | **1.000** |
| Sentence wrapped in `**markdown**` | deterministic | present | 1.000 |
| Sentence split across a line break | deterministic | present | 1.000 |
| SENTENCE IN UPPERCASE | deterministic | present | 1.000 |
| Sample copy + exact sentence appended | deterministic | present | 1.000 |
| Sample copy WITHOUT the sentence | deterministic | altered | 0.536 |
| Letter-spaced OCR artefact ("P a s t …") | **not fired** → LLM backstop | altered | 0.625 |

Markdown, punctuation, capitalisation, line breaks and whitespace are all
neutralised by `matcher.normalize()` (strips `[^\w\s]`, collapses whitespace,
lowercases). **None of the normalisation hypotheses explain the failure.**

### 3.2 The observed signature is only reachable when the phrase is absent

Two independent code paths agree:

- `deterministic_triggers` runs `re.search(r"\bpast performance\b", document_text,
  re.IGNORECASE)` over the **entire** joined chunk text. If the phrase were present
  in any case/markdown form, provenance would read `matched 'past performance'`.
  The screenshot shows **`llm:past_performance`** — which is only assigned when
  the deterministic trigger did NOT fire (triggers.py:187-192).
- `classify` gives 1.000 whenever the normalised sentence is present anywhere.
  A score of ~0.50 is the fuzzy floor `partial_ratio` produces when the needle's
  common English tokens ("performance", "is", "not", "of", "future") partially
  overlap unrelated text. Real uploaded PDFs in `backend/uploads` that contain
  no disclaimer at all score exactly 0.482–0.554.

Therefore the run that produced the screenshot analysed a document text that did
**not contain the phrase "past performance" in any form** — while the creative
visibly does. The sentence was lost between the file and `document_text`.

### 3.3 Extraction demonstrably drops footer/non-body disclaimers

Reproduced with the exact `_extract_docx` logic on a DOCX whose body carries a
returns claim and whose **footer** carries the approved disclaimer:

```
EXTRACTED: 'Bajaj Life Wealth Creator\n\nBetween 2022 and 2024 our equity-linked
            fund delivered an average of 22% per annum.'
contains 'past performance': False
FOOTER ACTUALLY IN FILE: 'Past performance is not indicative of future performance.'
```

`_extract_docx` iterates `Document(path).paragraphs` only — python-docx does not
include **headers, footers, text boxes, or table cells** in that collection.
Marketing creatives put mandated disclaimers precisely there.

The PDF path has the same class of gap: `_extract_pdf` is pdfplumber text-layer
only. A disclaimer rendered as an image, or as **outlined/vectorised text**
(standard for Illustrator/Canva-exported creatives), extracts as nothing. The
OCR fallback added in commit `8adf065` covers only the *comparison* path
(`comparison_service._pdf_ocr_lines`) — the *submission/analysis* preprocessing
path has none.

### 3.4 Why the LLM fired the obligation (correctly)

The disclosure backstop prompt (nodes.py:374-381) instructs the model:
*"Consider paraphrases … (e.g. 'our fund grew 12% last year' implies a
past-performance obligation)"*. A creative with return figures therefore
correctly carries the obligation. The LLM never judged the wording itself — it
only answered "does this document need a past-performance disclaimer?" The
answer yes was right. **The LLM did not override anything.**

### 3.5 Why the message said "present but altered" for an absent disclaimer

`classify` maps `0.45 ≤ sim < 0.85` (no anchors declared) to `altered`.
A 0.50 `partial_ratio` over a large document is **noise, not evidence of
presence** — yet `_disclosure_finding_to_violation` renders it as "Disclaimer
present but altered/incomplete… Replace with the approved wording", which reads
as an accusation that the visible wording is wrong. No evidence span is shown,
so the reviewer cannot see WHAT the engine thought was the altered disclaimer.

### 3.6 Why the suggested fix looked identical to the creative wording

`backend/data/disclaimers/past_performance.json` is contaminated from the Excel
ingestion (`source: "Copy of Disclaimers.xlsx"`):

```json
"type": "Past Performance **",
"text": "**Past performance is not indicative of future performance."
```

The `**` are footnote markers from the spreadsheet, not mandated wording. The
matcher's normaliser strips them (harmless for matching), but the UI renders
`suggested_fix` = the registry text — which after markdown rendering looks
*identical* to what is already printed on the creative, making the verdict
appear self-contradictory.

## 4. Root cause statement

1. **Primary cause — extraction loss (Stage: text extraction).**
   The mandated disclaimer, present in the creative's footer/graphic layer, is
   not extracted: DOCX headers/footers/text-boxes/tables are skipped by
   `_extract_docx`; image-based or outlined-text PDF regions are skipped by
   `_extract_pdf` (no OCR in the preprocessing path).
2. **Amplifier 1 — dishonest "altered" band.** The matcher labels fuzzy-floor
   noise (sim ≈ 0.5 over unrelated text) as "present but altered" with no
   evidence span, converting "we could not find it" into "your wording is wrong".
3. **Amplifier 2 — registry contamination.** `**` footnote artefacts in the
   rule's `type` and `text` make the UI verdict and the suggested fix confusing
   ("Past Performance **", fix visually identical to the creative).
4. **Non-causes (verified):** brand/entity mapping, category bifurcation, UIN
   resolution, chunking, retrieval, similarity thresholds, prompt bias, and LLM
   override play **no role** in this verdict path. The LLM's only contribution
   (obligation trigger) was correct.

## 5. Why similarity became exactly ~0.50

`fuzz.partial_ratio(required, doc)` slides the 56-char normalised needle over
the document and returns the best window's Indel ratio. Generic marketing prose
shares roughly half the needle's characters-in-order ("… performance …", "is",
"not", "of", "future" appear in most financial copy), so documents *without*
the disclaimer plateau at 0.48–0.55. 0.50 is the fingerprint of
**no genuine match anywhere** — not of a slightly-wrong disclaimer.

## 6. Fix plan (implemented in this change-set)

| # | Fix | File(s) |
|---|---|---|
| 1 | Extract DOCX headers, footers, tables and text boxes; label non-body provenance | `preprocessing_service.py` |
| 2 | OCR fallback for image/outlined-text PDF pages in the submission path (mirrors comparison path) | `preprocessing_service.py` |
| 3 | Evidence-gated verdicts: "altered" requires a real aligned span sharing distinctive tokens with the required text; otherwise "missing". Both raw and normalised similarity + the matched span are reported | `disclaimer/matcher.py` |
| 4 | Clean `**` artefacts from the registry record; audit all 10 records; load-time artefact guard | `data/disclaimers/*.json`, `disclaimer/registry.py` |
| 5 | Verdict provenance + explanation payload (match_method, evidence span, decision trace, counterfactual) | `nodes.py` |
| 6 | Regression tests for all of the above | `backend/tests/` (unversioned by repo policy) |

Change #4 does not alter mandated wording — it removes spreadsheet footnote
markers that were never part of the regulator-approved sentence. All other
changes leave rule text, thresholds, effective versions and severities intact.

---

## 7. Before / after — the failing case

| Case | Before | After |
|---|---|---|
| Creative with disclaimer in DOCX footer + returns claim in body | footer dropped at extraction → LLM fires obligation → **"altered 0.50 (llm:past_performance)"** | footer extracted → deterministic trigger → **present 1.000, exact_normalized_match** |
| Copy with NO disclaimer at all | "Disclaimer **present but altered** (similarity 0.54)" | honest "**missing**" (reason `fuzzy_floor_noise`), counterfactual explains extraction-vs-wording |
| Wording with "not" dropped (meaning inverted) | **present 0.96 — undetected tampering** | **altered** (reason `critical_token_lost`, names the lost word) |
| Genuine paraphrase ("not a guarantee of future results") | altered | altered (reason `partial_attempt`, evidence span shown) |
| Exact/markdown/case/line-split/smart-quote wording | present | present (unchanged) |

## 8. Files changed (with reasons)

**Backend code**
| File | Reason |
|---|---|
| `backend/app/services/disclaimer/matcher.py` | evidence-gated verdicts, `MatchDetails` explainability, legally-critical-token protection |
| `backend/app/services/disclaimer/registry.py` | load-time warning for `**` ingestion artefacts |
| `backend/app/services/agents/graph/nodes.py` | wire `match_details`; explainability metadata + counterfactual + provenance; evidence span in "altered" message; run-level `grounding_mix` |
| `backend/app/services/preprocessing_service.py` | extract DOCX footers/headers/tables/text-boxes; per-page PDF OCR fallback; drop hidden HTML elements; drop tracked-change residue in text boxes |
| `backend/app/services/comparison_service.py` | `draw_annots=False` — reviewer PDF annotations must not be OCR'd into content |
| `backend/app/services/fact_card_service.py` | UIN-collision detection (`collisions`, `get_all`, load-time warning) |
| `backend/app/services/product_resolver.py` | fuzzy leg emits one entry per UIN (budget bug); `ambiguous` + `candidates` on collided UINs |
| `backend/app/services/brochure_parser.py` | restore brand-era-invariant regexes (`(?:\s+Allianz)?`) broken by the rename find-and-replace |
| `backend/app/services/entity_registry.py` | NEW — canonical legal-entity/alias registry |
| `backend/scripts/verdict_sensitivity.py` | NEW — one-variable-at-a-time sensitivity harness (influence matrix) |

**Data / seeds / generated docs**
| File | Reason |
|---|---|
| `backend/data/disclaimers/past_performance.json` | removed `**` footnote artefacts from `type` and `text` |
| `backend/data/disclaimers/{general_product,generic_non_product,ulip_risk}.json` | fixed tautological legal footer ("Formerly known as **Bajaj Allianz** Life Insurance Company Limited") |
| `backend/data/product_fact_cards/116B056V01-trad-fpr-leaflet.json` | note wrongly called the CURRENT brand "legacy" |
| `backend/data/entities/balic.json` | NEW — canonical entity + aliases + effective dates + legal-footer form |
| `backend/scripts/seeds/bajaj_brand.yaml` | first-mention rule was self-nullifying after the rename; fixed + legacy-name guidance |
| `docs/guidelines-docs/disclaimers.md` | regenerated from the cleaned registry |

**Frontend**
| File | Reason |
|---|---|
| `frontend/lib/types.ts` | typed the explainability payload on `ViolationMetadata` |
| `frontend/components/review/ViolationCard.tsx` | provenance badge (deterministic vs hybrid) + match-evidence block (method, raw/normalised sim, closest span, counterfactual) |

**Docs** — `ROOT_CAUSE_ANALYSIS.md`, `VERDICT_EXPLAINABILITY.md`,
`docs/audits/2026-07-28-{brand-entity-audit,comment-annotation-audit,uin-duplication-report}.md`.

**Tests** (local only — `backend/tests` is gitignored by repo policy):
`test_disclosure_matching.py` (26), `test_disclosure_extraction.py` (8),
`test_product_resolution.py` (7), `test_brand_alias.py` (10),
`test_comment_separation.py` (6) — full suite 87 passing including the 31
pre-existing comparison tests.

## 9. Migrations / operator steps

- **No Alembic migration required.** No schema change; the explainability
  payload rides the existing `violations.violation_metadata` JSONB and run
  metadata.
- **Re-seed brand rules** on any environment whose `rules` table was seeded
  before this fix: `python -m scripts.seed_rules` (the corrected
  `bajaj_brand.yaml` first-mention rule).
- **Restart the backend** so the disclaimer/fact-card/entity registries reload
  from disk.
- Optionally re-run the guidelines ingestion for the regenerated
  `docs/guidelines-docs/disclaimers.md` (manual operator step, calls live
  embeddings).
- **Do not rewrite historical DB rows** (violations citing old wording are the
  audit trail).
- OCR in the submission path uses the existing `compare_ocr_*` settings and the
  tesseract binary; where tesseract is absent the engine logs a warning and
  behaves as before (text-layer only).
