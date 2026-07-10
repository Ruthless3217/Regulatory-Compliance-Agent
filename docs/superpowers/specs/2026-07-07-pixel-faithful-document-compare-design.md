# Pixel-Faithful Document Comparison — Design

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Area:** `backend/app/services/comparison_service.py`, new render pipeline, `frontend/components/compare/*`

## Problem

The comparison tool extracts text from each document and reflows it into a two-column word-diff. Users want to **see each document rendered as it actually looks** (fonts, tables, page layout) with change highlights painted on top — the way Draftable does — instead of reflowed plain text. The primary use case is a **Word template ↔ finalized PDF** of the same policy (cross-format).

The word-level diff engine is already solid (token-stream alignment, placeholder-aware; see `project_cross_format_comparison_2026-07-03`). What is missing is (a) faithful rendering of each document and (b) mapping each diffed word back onto its position in that rendering to highlight it in place.

## Goals

- Render both documents to **page images** and overlay **word-level change boxes** (red = removed/changed on the original, green = added/changed on the revised) at their true coordinates.
- Support the core **docx ↔ pdf** cross-format case, plus pdf↔pdf and docx↔docx.
- Keep the existing text-diff view; the pixel view is the default for file uploads, with a toggle. Paste/.txt keep the text view only.
- Reuse the existing token-alignment engine so both views share one alignment (and one placeholder policy).

## Non-goals

- Character-level (sub-word) highlighting. Word-level boxes only for v1 (character-level is a later refinement).
- Making the two sides line up page-for-page. Cross-format inherently differs in pagination/fonts; each side faithfully represents its own source.
- OCR of scanned PDFs (previously explored and removed). Scanned/no-text PDFs render pages with no boxes and a visible notice.
- Replacing the text-diff view or dropping paste support.

## Key decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Fidelity | Pixel-faithful page images + coordinate overlays | User wants true-Draftable look |
| Word→PDF rendering | **Gotenberg sidecar** (wraps LibreOffice), called over HTTP | Keeps Python image lean; isolated/replaceable; `docx2pdf` needs MS Word (won't run in Linux) |
| PDF→images | `pypdfium2` (already installed via pdfplumber, BSD) | No new heavy dep; avoids PyMuPDF's AGPL and Poppler's GPL |
| Word coordinates | `pdfplumber.extract_words()` (bboxes in PDF points) | Already a dependency; verified working |
| Views | Pixel default for uploads + toggle to text; paste/.txt = text only | Keeps all current work; nothing to render for pasted text |
| Processing | Text diff **synchronous** (instant); pixel render **async** via FastAPI `BackgroundTasks` | Heavy render (LibreOffice + N pages ≈ 5–20s) shouldn't block; Celery is rejected in this project |
| Coordinates | Stored in **PDF points** + page size; frontend positions boxes as % | Resolution/zoom-independent |
| Page cap | Configurable, default **60**; surplus surfaced as `truncated_pages` | No silent truncation |

## Architecture / data flow

```
input file ──► normalize to PDF ──► render pages → PNG (served)
                    │             └─ extract positioned words (bbox, PDF points)
   Word ─► Gotenberg (LibreOffice) ─► PDF
   PDF  ─► used directly

        old words ┐
                  ├─► token diff (shared engine) ─► per-word tags + placeholder suppression
        new words ┘        │
                  ┌────────┴─────────┐
             overlay model      diff blocks (existing text view)
          (boxes per page +
           changes list)
```

Both documents run the identical `input → PDF → (images + positioned words)` path. The **text view** continues to be driven by the current `extract_segments` + `build_diff` (computed synchronously in `POST`). The **pixel view** is driven by the positioned words from the normalized PDFs, diffed by the same alignment core, in the background task.

## Data model (migration 0014 — next after 0013_document_comparisons)

Add to `DocumentComparison`:
- `render_result` **JSONB**, nullable — overlay model (below).
- `render_status` **String(50)**, default `processing` — `processing | completed | failed | skipped`.
- `render_error` **Text**, nullable.

`diff_result` and `status` are unchanged (text view). `status=completed` as soon as the text diff is stored; the pixel render tracks separately via `render_status`.

### `render_result` schema

```jsonc
{
  "old": {
    "pages": [
      { "n": 1, "w_pt": 595.3, "h_pt": 841.9,
        "boxes": [ { "x0": 36, "y0": 26, "x1": 70, "y1": 40,
                     "type": "removed", "change_id": "c12" } ] }
    ]
  },
  "new": { "pages": [ /* ... "type": "added" ... */ ] },
  "changes": [
    { "id": "c12", "kind": "removed|added|modified",
      "old": { "page": 1, "bbox": [36,26,70,40], "text": "<XX>" },   // present for removed/modified
      "new": { "page": 1, "bbox": [40,26,60,40], "text": "10" } }     // present for added/modified
  ],
  "truncated_pages": 0
}
```

Page image bytes are **not** in JSON — they are files on disk, fetched via the pages endpoint. Boxes are in PDF points; `w_pt`/`h_pt` let the frontend scale.

**Box `type` is per-side only:** `removed` (drawn red on original pages) or `added` (drawn green on revised pages). A *modified* word contributes a `removed` box on the original **and** an `added` box on the revised, both carrying the same `change_id`, and its `changes` entry has both `old` and `new`. This matches the existing `ChangeCard`, which renders `removedText` and `addedText` together.

## API

- `POST /comparisons` — inputs unchanged. Computes text diff inline → `status=completed`. Sets `render_status=processing` for docx/pdf uploads, or `skipped` when no file has layout (paste/.txt on a side ⇒ skip pixel view). Schedules `render_comparison(id)` as a `BackgroundTask`. Returns the serialized comparison immediately (now including `render_status`).
- `GET /comparisons/{id}` — returns `diff_result`, `render_status`, and `render_result` (when ready). Frontend polls while `render_status=processing`.
- `GET /comparisons/{id}/pages/{side}/{n}` — `side ∈ {old,new}`, `n` 1-based; validates bounds and streams the PNG via `FileResponse`. 404 if absent.
- `DELETE /comparisons/{id}` — also removes `upload_dir/<id>/` (page images) in addition to the uploaded source files.

## Storage

```
<upload_dir>/<comparison_id>/old/page-0001.png, page-0002.png, ...
<upload_dir>/<comparison_id>/new/page-0001.png, ...
```
Page image URLs are derivable from id/side/n, so `render_result` stores only counts + geometry, not paths.

## New / changed backend modules

- `services/gotenberg_client.py` — `convert_to_pdf(src_path) -> bytes` via Gotenberg `POST /forms/libreoffice/convert`. Config `settings.gotenberg_url`. Raises a typed error on non-200/timeout.
- `services/pdf_render_service.py`
  - `to_pdf(file_path, content_type) -> pdf_path` — docx via Gotenberg; pdf passthrough; (txt ⇒ not rendered).
  - `render_pages(pdf_path, out_dir, cap) -> list[PageMeta]` — pypdfium2 render at fixed scale to PNG; returns `(n, w_pt, h_pt)` per page; respects cap.
  - `positioned_words(pdf_path) -> list[Word]` — pdfplumber `extract_words()` per page → `Word(text, norm, page, x0, y0, x1, y1)`; reuse running-header/footer + page-number stripping on the word list (drop words whose line repeats across pages / matches page-number pattern).
- `services/comparison_service.py` — add `word_level_ops(old_words, new_words) -> (old_tagged, new_tagged, changes)`: normalize each positioned word via `_norm_token`, align with the existing `SequenceMatcher` core, tag each word `equal|removed|added|changed`, apply `_suppress_placeholder_fills`, and group contiguous non-equal words into `changes`. Shares `_tokenize`/`_norm_token`/alignment with `build_diff`.
- `services/comparison_render.py` (or route helper) — `render_comparison(id)`: for each side `to_pdf → render_pages + positioned_words`; `word_level_ops`; build `render_result` (map word bboxes → per-page boxes, changes); save; set `render_status`. On any failure → `render_status=failed`, `render_error`.

## Frontend

- `components/compare/PixelDiffViewer.tsx` — two columns, each a vertical stack of `<img>` page images (lazy-loaded); over each page, absolutely-positioned semi-transparent boxes (`left/top/width/height` as % of `w_pt`/`h_pt`) — red for `removed` boxes on the original, green for `added` boxes on the revised.
- `components/compare/CompareWorkspace.tsx` — add a **Pixel | Text** toggle. Default Pixel when `render_result` exists; else Text. While `render_status=processing`, poll `GET /comparisons/{id}` (e.g. every ~2s) and show a skeleton/progress state. `render_status=failed|skipped` ⇒ Text view with an inline note.
- `components/compare/ChangesPane.tsx` — unchanged API; fed by `deriveChanges(blocks)` in text mode or by `render_result.changes` in pixel mode. Selecting a change scrolls the active view to its box/row and pulses it (mirrors current behavior).
- `lib/types.ts` — add `RenderResult`, `PageMeta`, `Box`, `RenderChange`.

## Error handling / edge cases

- **Gotenberg unavailable / convert error** → `render_status=failed` + `render_error`; UI falls back to Text view with a note. Text view is unaffected (computed inline).
- **Scanned / no-text PDF** → pages render, zero boxes; UI shows "No extractable text — showing pages without word highlights."
- **Large docs** → render up to `cap` (default 60) pages/side; extra pages counted in `truncated_pages` and surfaced in the UI.
- **Cross-format pagination mismatch** → expected; documented in the UI ("each side reflects its own source").
- **Coordinate origin** → pdfplumber `top`/`x0` and pypdfium2 render both use a top-left origin; validate the transform against a known fixture during implementation.

## Testing (TDD)

- `word_level_ops`: equal/removed/added/changed tagging; placeholder-fill suppression; segmentation-drift robustness; `changes` grouping.
- overlay builder: bbox → per-page boxes; header/footer/page-number word stripping; `truncated_pages`.
- `gotenberg_client`: mocked HTTP (200 → bytes; error → typed raise). No live Gotenberg.
- `positioned_words`: mocked pdfplumber pages. No live rendering in unit tests; a tiny committed fixture PDF may back one integration-style test.
- API: `render_status` transitions; pages endpoint bounds/404; delete removes image dir.

## Rollout / deployment

- Add a **Gotenberg service** to `docker-compose` (e.g. `gotenberg/gotenberg`), backend gets `GOTENBERG_URL=http://gotenberg:3000`. Forward the env var per the two-.env pattern (`feedback_docker_env_ops`).
- Backend image bakes code (no volume mount) ⇒ rebuild backend after changes to see them live.
- New Python deps: none required for rendering (pypdfium2/Pillow present); confirm `pypdfium2` is pinned in `requirements.txt` (currently transitive via pdfplumber) so it can't drift.
- No live API/LLM calls in this feature (`feedback_no_live_api_calls`). Not auto-committed (`feedback_no_auto_commit`).

## Open risks

- Gotenberg rendering fidelity for complex Word tables — validate on the real eTouch template early.
- Render latency on very large policy PDFs (perf test; cap mitigates).
- `pypdfium2` render scale vs pdfplumber point coordinates — pin the transform with a fixture test before building the overlay.
