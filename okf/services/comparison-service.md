---
type: Service
title: Document Comparison Service
description: A standalone, LLM-free tool that diffs two document versions at the word-token level (cross-format, placeholder- and move-aware) with word-level highlighting, plus a PDF-only pixel render pipeline and export artifacts.
resource: backend/app/services/comparison_service.py
tags: [service, comparison, diff, standalone, render, export, moves]
timestamp: 2026-07-12T00:00:00Z
---

# Document Comparison Service

`backend/app/services/comparison_service.py`. Powers the standalone **Compare** feature. **Pure text processing — no LLM.**

## How it works (current — word-token alignment)

- `extract_segments` dispatches by content type: DOCX via python-docx (headings prefixed `## `, tables incl. nested walked),
  PDF via pdfplumber (running headers/footers + page numbers stripped, line-wraps de-hyphenated), else blank-line split →
  sentence segments. (The older `extract_paragraphs` family still exists but the route uses the segment path.)
- `build_diff` flattens both sides to a normalized `(display, match_key)` token stream and aligns with
  `difflib.SequenceMatcher`; identical prose stays aligned across formats even when the two extractors chunk differently.
  Template placeholders (`<field>`, `____`, `XXXX`) collapse to a shared wildcard so filling one in is not a change. Token
  opcodes re-group into sentence-sized `equal` / `delete` / `insert` / `replace` blocks.
- **Move detection** (`detect_moves_in_blocks`, `_detect_moves_in_changes`): a delete-run and insert-run with identical
  normalized text (≥ 5 tokens) are paired — blocks gain `moved:true` + shared `move_id`; pixel changes merge into one
  `moved` change carrying both refs. Display-only; the diff is unchanged when no moves exist.
- `word_level_ops` aligns two positioned-word streams (shares the normalizer) for the pixel overlay.
- `match_query_in_words` is the pure per-page search matcher (case-insensitive substring → covered-words union bbox).
- Result persisted on [`document_comparisons.diff_result`](../data-model/document-comparisons.md) (JSONB); status
  `completed` / `failed`.

## Pixel render + export (companions)

- [`render_orchestrator.py`](./index.md) — `run_render(id)` FastAPI BackgroundTask, **PDF-only** (else `render_status=skipped`):
  `pdf_render_service` renders page PNGs + positioned words → `word_level_ops` → `RenderResult` (pages+boxes+changes) on
  `document_comparisons.render_result`. `pdf_render_service.to_pdf` is PDF-passthrough only; `gotenberg_client.py` stays
  unimported (DOCX→PDF re-enable seam).
- `export_service.py` — `changes-report.docx` (python-docx), `old/new-highlighted.pdf` + `side-by-side.pdf` (Pillow box
  burn-in → img2pdf over rendered PNGs), `bundle.zip`.

## Related

- Exposed by the [Comparisons API](../api/index.md) (`/comparisons`) — incl. `pages`, `search`, `annotations`, `export`, `rerun`.
- Rendered by the full-screen [Compare viewer](../frontend/routing.md) (`(viewer)` route group).
