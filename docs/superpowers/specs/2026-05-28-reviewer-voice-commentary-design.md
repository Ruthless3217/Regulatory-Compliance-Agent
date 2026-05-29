# Reviewer-Voice Commentary & Novel-Finding Coverage — Design Spec

**Date:** 2026-05-28
**Branch:** `feat/precedent-compliance-engine` (continuation)
**Author:** AI marketing team / Bajaj Allianz Life

## Problem

The precedent-driven compliance engine ([[2026-05-25-precedent-compliance-engine-design]]) produces violations whose user-visible `description` field reads like meta-commentary on the precedent rather than commentary on the document:

> "The new chunk mentions tax-free maturity benefits, which is similar to the precedent that flagged a claim about life insurance."

Real Bajaj reviewers (per the comments in `GEO content for Smart Secure.docx` and `re-29200_GEO content for Smart Secure.docx`) write very differently — short, imperative or interrogative, naming the exact phrase, prescribing specific compliant text or specific evidence:

- *"Use 'Terminal Illness with Term Booster' in entire document"*
- *"Has UW approved this? Pls share approval on tool"*
- *"Include clear information Switching between fund under Investor Selectable Portfolio Strategy or investment portfolio strategies is free of the Miscellaneous Charge.. portfolio strategies can be switched only during policy anniversary"*
- *"Lockin- period Ulip disclaimer missing"*

Two failures follow from this:

1. **Voice failure** — the engine sounds AI, not like a reviewer.
2. **Coverage failure** — when retrieval surfaces zero precedents for a chunk, `nodes.analysis_node` silently returns zero violations (`nodes.py:282-283`). Issues outside the corpus are missed entirely.

## Goal

Produce commentary that:

1. Reads exactly like a Bajaj compliance reviewer would write about *this* document.
2. Carries the *substance* the reviewer would supply — exact prescribed text, section names, charge names, conditions, specific artifacts to share.
3. Catches issues *not* covered by retrieved precedents (novel findings) with the same reviewer voice and expanded rationale grounded in a named regulatory basis.

## Non-Goals

- Changing the rule-based scoring layer or grading scheme.
- Replacing the existing precedent corpus or RAG pipeline.
- Persisting reviewer names. Bajaj-side privacy stance unchanged.
- Surfacing new analytics columns in the dashboard (action_type stays in JSONB for v1).

## Approach (B): prompt + schema + corpus filter

Three coordinated changes:

1. **Schema** — `PrecedentCitation` gets a `reviewer_comment` field (replaces `description`), an `action_type` enum, and an optional `evidence_needed`. A new `NovelFinding` model carries the same fields plus `regulatory_basis` and a confidence floor. Both flow into the existing `violations` row via `violation_metadata` JSONB — no DB migration.
2. **Prompt** — `create_precedent_prompts` is rewritten with reviewer-voice examples drawn directly from the Smart Secure docs and an explicit ban on meta-bridge phrasing. Adds a novel-finding section that requires `regulatory_basis` and ≥0.75 confidence.
3. **Corpus filter** — one-time alembic migration drops pure-response precedents (`done`, `ok`, `added`, `edited`, …) from `rag_compliance_examples`; a runtime guard in `precedent_retriever` repeats the filter as a safety net.

## Schema

`backend/app/schemas/compliance_schemas.py`:

```python
ACTION_TYPES = Literal[
    "rewrite", "share-evidence", "add-disclaimer", "verify-source", "remove",
]

class PrecedentCitation(BaseModel):
    """LLM cites a retrieved precedent. Reviewer comment is the LLM's
    adaptation of the historical reviewer's substance + tone to the new chunk."""
    precedent_index: int = Field(..., ge=0,
        description="Zero-based index into the precedents list.")
    current_text: str = Field(..., min_length=1,
        description="EXACT phrase from the NEW chunk this precedent flags. Verbatim, no paraphrase.")
    reviewer_comment: str = Field(..., min_length=10,
        description=(
            "1-2 sentences in reviewer voice (imperative/interrogative). "
            "Includes specifics the precedent supplied: exact prescribed text, "
            "section names, charges, conditions. Never meta — never 'similar "
            "to a precedent that…'."))
    action_type: ACTION_TYPES
    evidence_needed: Optional[str] = Field(None,
        description="Short noun phrase if an external artifact is named, e.g., "
                    "'UW approval', 'latest fact sheet', 'Tax team approval'. "
                    "Null when action_type is rewrite/remove without artifact.")
    confidence: float = Field(0.85, ge=0.0, le=1.0)


class NovelFinding(BaseModel):
    """Issue clearly present in the new chunk but not covered by any retrieved
    precedent. Requires regulatory grounding and higher confidence."""
    current_text: str = Field(..., min_length=1)
    reviewer_comment: str = Field(..., min_length=20,
        description=(
            "2-4 sentences: flag + reasoning + specific action. Longer than "
            "precedent-grounded comments because there is no historical "
            "reviewer to point at — this comment carries the rationale."))
    action_type: ACTION_TYPES
    evidence_needed: Optional[str] = None
    regulatory_basis: str = Field(..., min_length=10,
        description=(
            "Specific rule/section the issue violates. Examples: "
            "'IRDAI ULIP regulations — Miscellaneous Charge disclosure', "
            "'Section 41 Insurance Act — no rebate/inducement claims', "
            "'IRDAI Advertisement Regulations 2021 — past performance disclaimer'."))
    confidence: float = Field(..., ge=0.0, le=1.0)


class PrecedentCitationsResult(BaseModel):
    citations: List[PrecedentCitation] = Field(default_factory=list)
    novel_findings: List[NovelFinding] = Field(default_factory=list)
```

### Persistence (no migration)

`Violation` columns unchanged. New tags live in `violation_metadata` JSONB:

```json
{
  "action_type": "share-evidence",
  "evidence_needed": "UW approval",
  "regulatory_basis": "IRDAI ULIP regulations — Miscellaneous Charge disclosure",
  "grounding": "precedent" | "novel"
}
```

`description` Text column is repurposed to carry `reviewer_comment` (it already drives the UI's main violation text — minimal frontend change). For novel findings, `cited_precedent_id`, `cited_anchor_text`, `cited_comment_verbatim`, `cited_final_text`, `similarity_score` are all `NULL`.

## Prompt

`backend/app/services/preprocessing_service.py::create_precedent_prompts`:

```
You are a senior Bajaj Allianz Life compliance reviewer (Legal/Compliance/FPU).
Your past colleagues' comments on similar copy are below — they show the
substance you should be checking for AND the voice you should write in.

For the NEW DOCUMENT SECTION:

(A) Decide which historical PRECEDENTS apply to this chunk. For each one,
    write `reviewer_comment` AS THE REVIEWER would write it about THIS chunk —
    name the offending phrase, state what's missing or wrong, and if the past
    reviewer prescribed specific compliant text or named a specific artifact,
    INCLUDE THOSE SPECIFICS in your comment.

(B) Separately, decide if any issue is clearly present in this chunk that NO
    listed precedent covers (e.g., a missing IRDAI-mandated disclaimer, an
    unverified statistic, a Section 41 inducement claim, ULIP charges not
    disclosed). Emit those under `novel_findings`. Novel findings REQUIRE
    a `regulatory_basis` and confidence ≥ 0.75. Do not invent findings.

DO NOT write meta-bridges like "this chunk is similar to a precedent that…"
or "the precedent flagged X". Write as if YOU are the reviewer reading this
document for the first time. The reader does not see the precedents.

PRECEDENTS:
{precedents_block}

NEW DOCUMENT SECTION:
{content}

ACTION TYPES (pick one per finding):
  rewrite         — use standardized terminology or insert prescribed text
  share-evidence  — produce an approval or source artifact (UW / Tax / PO / BI)
  add-disclaimer  — insert a missing regulatory disclaimer
  verify-source   — clarify provenance, match against authoritative document
  remove          — strip out non-compliant claim

VOICE EXAMPLES (these are the gold standard — match this style):

EXAMPLE 1 (rewrite — prescribes specific text):
  Precedent comment: "Include clear information Switching between fund under
    Investor Selectable Portfolio Strategy or investment portfolio strategies
    is free of the Miscellaneous Charge.. portfolio strategies can be switched
    only during policy anniversary"
  New chunk says: "...allows you to switch between different investment funds
    based on your financial goals and market outlook..."
  reviewer_comment: "Include clear information: switching between funds under
    Investor Selectable Portfolio Strategy is free of the Miscellaneous
    Charge; portfolio strategies can be switched only on policy anniversary."
  action_type: "rewrite"
  evidence_needed: null

EXAMPLE 2 (share-evidence):
  Precedent comment: "Has UW approved this? Pls share approval on tool"
  New chunk says: "...comprehensive life coverage up to ₹3 Crore..."
  reviewer_comment: "Has UW approved the ₹3 Crore SA? Pls share approval on tool."
  action_type: "share-evidence"
  evidence_needed: "UW approval"

EXAMPLE 3 (add-disclaimer):
  Precedent comment: "Lockin- period Ulip disclaimer missing"
  New chunk says: "...invest in our Equity Growth Fund for long-term wealth..."
  reviewer_comment: "ULIP lock-in period disclaimer missing for this Equity
    Growth Fund mention."
  action_type: "add-disclaimer"
  evidence_needed: "ULIP lock-in disclaimer"

EXAMPLE 4 (verify-source):
  Precedent comment: "Pl match it with latest fact sheet"
  New chunk says: "3.47 Crore Lives Covered | 99.33% Claim Settlement Ratio"
  reviewer_comment: "Match these stats with the latest fact sheet before
    publication."
  action_type: "verify-source"
  evidence_needed: "latest fact sheet"

EXAMPLE 5 (novel — no precedent retrieved, expanded reasoning):
  No precedent in the list covers GST claims.
  New chunk says: "GST is not applicable on individual life insurance premium
    as per Government Notification 16/2025."
  reviewer_comment: "Tax claim cites Notification 16/2025 — but this is an
    external regulatory notification, not a Bajaj product feature. Share Tax
    team approval substantiating both the notification number and the scope
    (does it cover ULIP, term, endowment, or all individual life?) before
    publication. If the scope is narrower than implied here, the claim must
    be qualified."
  action_type: "verify-source"
  evidence_needed: "Tax team approval + scope confirmation"
  regulatory_basis: "IRDAI Advertisement Regulations 2021 — tax claim substantiation requirement"
  confidence: 0.85

OUTPUT
Return JSON matching the schema. Emit one `citations` entry per applicable
precedent. Emit `novel_findings` for present-but-uncovered issues only.
```

### Empty-precedents case

`nodes.analysis_node` no longer early-returns on empty precedents (the current `nodes.py:282-283` short-circuit goes away). When `precedents == []`, the prompt is rendered with an empty `PRECEDENTS` block and an explicit note that the LLM should emit `novel_findings` only. This is what closes the "this wasn't given to me" gap.

## Corpus filter

### One-time migration

`backend/alembic/versions/0007_drop_response_precedents.py`:

- Computes `LOWER(REGEXP_REPLACE(TRIM(comment_text), '[.!?]+$', ''))` per row.
- Deletes rows where the normalized value is in `RESPONSE_DENYLIST`.
- Logs deleted IDs into a new `_purged_response_precedents` table for audit and downgrade reversibility.

Denylist (final v1 set):

```
RESPONSE_DENYLIST = frozenset({
    "done", "ok", "okay", "yes", "no",
    "noted", "agreed", "agree", "fine", "accepted", "approved", "confirmed",
    "added", "edited", "deleted", "revised", "rephrased", "checked", "check",
})
```

Expected impact: ~600-800 rows removed (12-15% of the 5,323-row corpus). Concise reviewer flags like `source?`, `rephrase.`, `how?`, `what?`, `pls add source` are preserved.

### Runtime guard

`backend/app/services/rag/retrievers/precedent_retriever.py` adds:

```python
_RESPONSE_TOKENS = frozenset({...})  # same denylist

def _is_thin(precedent: Dict) -> bool:
    c = (precedent.get("comment_text") or "").strip().lower().rstrip(".!?")
    return c in _RESPONSE_TOKENS or len(c) < 3
```

Applied immediately after the vector search in `retrieve_per_chunk` before the top-K cap. Cheap, idempotent with the migration, and protects against bad future ingests.

## `nodes.py` mapping

Both `citations` and `novel_findings` flow through the same violation-shape mapper (see Section 4 of brainstorm). Key invariants:

- Precedent citations: `cited_*` columns populated; `violation_metadata.grounding = "precedent"`.
- Novel findings: `cited_*` columns NULL; `violation_metadata.grounding = "novel"`; `violation_metadata.regulatory_basis` set; `confidence < 0.75` dropped before persistence.
- The early-return on empty precedents is replaced by a "novel-only" LLM call (no precedents block, only `novel_findings` allowed in output).

## Frontend

`frontend/src/.../ViolationCard.tsx` (minimal additions):

- Main violation text already renders `description` → automatically becomes reviewer voice.
- Below the text, a badge row:
  - `Action: share-evidence` (color-coded per action_type)
  - `Needed: UW approval` when `evidence_needed` is non-null
  - `Source: precedent` (linkable to historic ticket) or `Source: novel · IRDAI ULIP regs` (regulatory_basis as plain text)
- "View original ticket" pane unchanged for precedent-grounded violations.
- For novel violations, replaces that pane with: "Novel finding — no historical match. Regulatory basis: {regulatory_basis}."

## Tests

### Unit tests (`backend/tests/test_precedent_prompt_v2.py`)

1. `test_reviewer_voice_prompt_structure` — `create_precedent_prompts` output contains the negative constraint and all 5 voice examples; structural assertions only, no LLM call.
2. `test_novel_finding_requires_regulatory_basis` — Pydantic validation: `NovelFinding(regulatory_basis="")` raises.
3. `test_novel_finding_confidence_floor` — the mapper in `nodes.py` filters out novel findings with `confidence < 0.75`.
4. `test_response_token_filter` — `_is_thin({"comment_text": "done."})` → True; `_is_thin({"comment_text": "source?"})` → False.
5. `test_alembic_0007_dry_run` — fixture KB seeded with 4 pure-response + 4 real rows; up-migration removes only the pure-response rows; downgrade restores via audit table.
6. `test_empty_precedents_emits_novel_only` — when retrieval returns `[]` for a chunk, the prompt rendered for the LLM call contains no PRECEDENT block and an explicit instruction to emit only novel findings.

### Golden integration test (`backend/tests/test_smart_secure_golden.py`)

Hand-build a 6-chunk synthetic submission from the two Smart Secure docs, where each chunk has a known expected reviewer comment (the actual comment from the docx). Run end-to-end through analysis with the new prompt and assert:

- Reviewer comment contains the prescribed substance for example 1 (e.g., the literal string "Investor Selectable Portfolio Strategy" appears in the violation description for the fund-switching FAQ chunk).
- `violation_metadata.action_type` matches the expected bucket per chunk.
- `evidence_needed` populated for share-evidence chunks (e.g., "UW approval" for the ₹3 Crore chunk).
- No violation description contains the literal strings `"similar to a precedent"` or `"the precedent flagged"`.
- At least one chunk produces a `novel` finding with a non-empty `regulatory_basis`.

This locks the user-visible behavior to the specific corpus examples that motivated this work.

## Eval re-run

After the changes land, re-run `eval_precedent_replay --eval-frac 0.1` and compare against the 2026-05-27 baseline:

| Metric | Baseline (9/10 docs) | Direction expected | Why |
|---|---|---|---|
| precision_presence | 0.21 | ↑ | Corpus filter removes noise; better grounding |
| recall_presence | 0.66 | ↑ slightly | Novel findings catch chunks retrieval missed |
| anchor_recall | 0.35 | ↑ | Reviewer-voice forces exact-phrase grounding |
| missed_criticals | 32 | ↓ | Novel-mode catches structural disclaimer gaps |
| generated / real | 3.0× | ≈ unchanged or modest ↑ | 0.75 floor + reg-basis requirement constrains hallucination |

A regression on `precision_presence` would mean novel findings are hallucinating — that's the canary metric we watch.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM still emits meta-bridges despite the negative constraint | Golden test asserts forbidden strings absent; corrective-retry loop in `analysis_node` already exists and can be extended |
| Novel-finding hallucination at scale | 0.75 confidence floor + required `regulatory_basis` + eval canary on precision |
| Corpus filter accidentally removes good short comments | Denylist is conservative (response tokens only); migration is reversible via audit table |
| Frontend renders raw JSONB metadata unsafely | Existing `ViolationCard` already renders untrusted strings; reuse same escaping; no new XSS surface |

## Open questions for future iterations

- Should novel-finding severity be LLM-emitted (currently hard-coded "moderate")?
- Should `action_type` and `evidence_needed` graduate from JSONB to typed columns once the dashboard adds filters?
- Should the novel-finding `regulatory_basis` be cross-checked against the `rag_source_docs` corpus (regulator quote retrieval) to ground the citation?

## References

- Source docs that motivated this work: `GEO content for Smart Secure.docx` (10 comments), `re-29200_GEO content for Smart Secure.docx` (25 comments).
- Prior spec: [[2026-05-25-precedent-compliance-engine-design]]
- Baseline eval: `backend/logs/eval_replay.json` (9 docs, quota-exhausted).
