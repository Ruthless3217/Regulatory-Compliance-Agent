# Review workspace: end-to-end document review, edit, re-run, export

Date: 2026-08-03
Status: implemented, except on-demand AI rewrite (see Not built)

## Problem

The module behaves as a grading tool, not a workflow. Three concrete failures:

1. An uploaded DOCX renders as flat extracted text ("like an MD filing"), not
   as the real document.
2. A reviewer cannot edit the document the way they would edit a Word file.
3. Nothing forces a re-analysis after an edit, so a corrected document can be
   exported still carrying the findings of the version before the correction.

## Root cause of (1)

Not the renderer. DOCX is never rendered at all.

```
submission_render_service.py:53   renderable = content_type == "pdf" ...
                                  otherwise -> page_render_status = "skipped"
ReviewTab.tsx:141                 usePdfPane = content_type === "pdf"
                                            && page_render_status === "completed"
```

A DOCX upload is skipped by the renderer, so the frontend falls back to
`DocumentPane`, which shows extracted plain text. `pdf_render_service.to_pdf`
already converts DOCX to PDF through the Gotenberg sidecar and is already used
by `submission_export_service`. The conversion seam exists; the renderer gates
it out before reaching it.

Swapping in a different PDF renderer, backend or frontend, would not change
this.

## Design

### 1. Render every uploaded document

`submission_render_service.run_render` routes DOCX through
`pdf_render_service.to_pdf` before rasterizing, instead of skipping it.
`page_render_status` stays the single source of truth; `usePdfPane` drops its
`content_type` clause and keys off the status alone. `skipped` remains the
outcome for formats with no page layout.

### 2. View / Edit toggle

A faithful render is rasterized page images, which cannot be edited in place.
Rather than compromise both, the pane carries an explicit toggle:

- **View** — page PNGs plus violation boxes positioned from
  `anchor_page`/`anchor_bbox`. Read-only. This is today's `PdfPagePane`.
- **Edit** — extracted text with span editing. This is today's `DocumentPane`.

Rejected: a docx to HTML to rich-editor to docx round-trip. It needs new
dependencies and silently drops what HTML cannot express (headers/footers,
section breaks, nested tables, embedded objects).

Saving in Edit mode creates a revision, which re-renders, so View reflects the
edit.

### 3. Re-run is mandatory after an edit

A new revision marks the latest compliance check stale. While stale the
workspace shows a banner, and export and approval are blocked until the
document is re-analysed. Findings and the document can never disagree in an
exported artifact.

### 4. Export preserves original formatting

`submission_export_service._clean_docx` currently builds a fresh `Document()`
from extracted text, discarding fonts, tables, images, and page structure. It
instead copies the uploaded DOCX and applies accepted edits into that copy, so
the original file is the base rather than a plain-text reflow. PDF export keeps
going through Gotenberg from that DOCX.

### 5. Reviewer navigation

Previous/Next violation controls, a reviewed-progress count, alongside the
existing filters and severity treatment.

### 6. Chat removed

Delete `components/chat/*`, the `/submissions/[id]/chat` route, the
`SubmissionHeader` link, `api/routes/chat.py`, and
`rag/retrievers/chat_retriever.py`. `chat_retriever` has exactly one importer
(`chat.py`) and there are no chat DB models, so the cut is clean. The
`_approved_source_passages` filter added in `e6304b4` existed to protect chat
retrieval and is removed with its only consumer.

## Not built

**On-demand AI rewrite.** A reviewer still gets only the `suggested_fix` the
analysis produced; there is no in-workspace "rewrite this again with my
instruction" action. `settings.chat_llm_model` and `llm_service`'s non-analysis
profile were kept precisely so this has somewhere to land.

## Out of scope

Version history, audit trail, and model-improvement feedback already exist
(`SubmissionRevision`, `DocumentComment`, `rule_feedback_service`,
compare-viewer). No work planned unless they prove broken in use.

## Verification

- `cd backend && python -m pytest -q`
- `cd frontend && npx tsc --noEmit && npx next build --no-lint`
- Manual: upload a DOCX, confirm it renders with real layout; edit it; confirm
  the stale banner blocks export; re-run; export DOCX and confirm formatting
  survived.
