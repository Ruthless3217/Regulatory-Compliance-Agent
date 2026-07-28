# Comment & Annotation Separation Audit

**Date:** 2026-07-28 · Phase-3 investigation: can reviewer comments, annotations
or hidden text enter the text used for compliance verdicts?

## Surface matrix (verdict text = joined submission chunks)

| Format | Surface | Extracted? | Reaches verdict? | Status |
|---|---|---|---|---|
| PDF | body text layer | yes | yes | correct |
| PDF | /Annots (sticky notes, FreeText, stamps) on texty pages | no (pdfplumber reads content stream only) | no | pinned correct |
| PDF | annotations on image-only pages via OCR fallback | **was YES** (pypdfium2 renders annots by default) | **was YES — LEAK** | **FIXED**: `render(..., draw_annots=False)` |
| PDF | form fields | no | no | correct |
| DOCX | body ¶/tables/text-boxes/headers/footers | yes (since this change-set) | yes | correct — consumer-visible |
| DOCX | Word comments (`word/comments.xml`) | no | no | pinned correct |
| DOCX | tracked deletions (`w:delText`) | no | no | pinned correct |
| DOCX | tracked-change residue inside text boxes (`w:moveFrom`) | **was yes** | **was yes — leak** | **FIXED**: ancestor filter |
| HTML | `<!-- comments -->` | no (bs4 drops) | no | pinned correct |
| HTML | `display:none` / `hidden` / `aria-hidden` / `font-size:0` | **was YES** | **was YES — LEAK** | **FIXED**: hidden-element filter + log |
| HTML | meta/og/h1/h2 | yes, deliberate, labeled `[META TAGS]` | yes | by design |
| MD/TXT/paste | everything verbatim | yes | yes | inherent to paste — see gaps |
| PPTX / speaker notes | unsupported upload type | n/a | n/a | — |

## Where comments legitimately influence verdicts

The **precedent lane** treats reviewer comments as first-class evidence, cleanly
separated end-to-end: `word/comments.xml` → `compliance_comments` (never mixed
with `draft_text`) → `comment_text`/`anchor_text` columns → prompt block
labelled "Reviewer comment:" inside an untrusted-data fence → verdicts carry
`cited_comment_verbatim` + `violation_metadata.grounding="precedent"`. The
evidence-grounding guard (`verify_evidence_grounding`) drops any finding whose
`current_text` is not literally present in the graded chunk, so historical
comment text cannot masquerade as creative text while the critic lane is on.

**New in this change-set:** run-level `metadata.grounding_mix` (a census of
verdict origins — precedent / rule / novel / disclosure / product_fact) is
computed and logged after analysis, making "reviewer comments influenced N
verdicts in this run" a queryable fact rather than a per-row reconstruction.

## Remaining gaps (documented, not fixed here)

1. **Paste/markdown passthrough.** Text pasted into New Analysis is graded
   verbatim — reviewer notes inside it become creative content. Durable fix:
   tag comment-grammar lines (`^\[.*\]:`, `^(Reviewer|Legal|Compliance):`) with
   a `surface="comment"` chunk-metadata value and route them to the audit lane.
2. **Surface labels are inline strings.** `[PAGE FOOTER]` / `[TEXT BOXES]` /
   `[META TAGS]` markers live inside the graded text instead of
   `chunk_metadata.surface` — graders cannot filter on them programmatically.
3. **Evidence-grounding guard is gated by `critic_enabled`.** It is pure and
   deterministic; it deserves its own always-on flag.
4. **Extraction asymmetry:** Compare includes tracked insertions; Compliance
   drops them (python-docx `CT_P.text`), so an accepted-revision draft is graded
   on pre-revision body text.

Each gap changes grading behaviour beyond the demonstrated Past Performance
root cause, so per the working rules they are recorded here for a follow-up
change with its own tests rather than bundled silently into this fix.
