# Compare Viewer Clone — Design Spec

**Date:** 2026-07-10
**Status:** Approved approach B (fresh viewer module); user waived per-section gates and asked to proceed.
**Sources:** `docs/COMPARE-TOOL-FEATURE-LIST.md` (target feature inventory), `docs/DOCUMENT-COMPARISON.md` (current-state audit).

## 1. Goal

Clone the Draftable-Desktop-style document comparison experience into the existing Compare
feature. The viewer opens **in a new browser tab as a full-viewport page** (no workspace
sidebar/topbar). Full subsystem scope: pixel document view, notes & tags, export pack, and
power extras (moves, per-pane zoom/search, scroll lock, single mode, adjust comparison).

**Hard constraint:** no new containers. Consequence: **PDF-only page rendering** — pixel
view activates only when BOTH sides are PDF; DOCX/text sides fall back to the text redline.
`gotenberg_client.py` remains on disk, unimported, as the future DOCX re-enable seam
(config `gotenberg_url` retained).

**Execution directives from the user:** navigate via the OKF bundle; use **Opus** for
implementation task delegation and **Fable** for planning agents.

## 2. Routing & page architecture

- New route group `frontend/app/(viewer)/` with its own minimal layout (full-viewport
  container; root layout already supplies fonts/toaster).
- `compare/[id]/page.tsx` **moves** from `(workspace)` to `(viewer)`. URL stays
  `/compare/[id]`; only the shell changes. No route collision: `(workspace)` keeps
  `/compare` (list) and `/compare/new` (intake).
- List rows and the post-create redirect open the viewer with `target="_blank"` /
  `window.open` — one browser tab per comparison replaces the desktop app's tab bar.

## 3. Backend

### 3.1 API (existing `/comparisons` router; every endpoint `require("comparison:use")`)

| Endpoint | Behavior |
|---|---|
| `POST /comparisons` | Unchanged inputs. After the (still-synchronous) text diff commits, schedule a **render BackgroundTask** if both sides are PDF, else set `render_status="skipped"`. `_serialize` now includes `render_status`, `render_result`, `render_error`. |
| `GET /comparisons/{id}` | Adds render fields + `annotations` (list). |
| `GET /comparisons/{id}/pages/{side}/{n}` | `FileResponse` PNG from `upload_dir/renders/{id}/{side}/page-%04d.png`. 404 if missing. |
| `GET /comparisons/{id}/search?side=old|new&q=` | On-demand pdfplumber word-scan of that side's stored PDF. Case-insensitive substring over the word stream; returns `[{page, bbox:[x0,y0,x1,y1]}]`, capped 200. 409 if that side is not a PDF. |
| `POST /comparisons/{id}/annotations` | Upsert `{change_id, note?, tags?}`. |
| `DELETE /comparisons/{id}/annotations/{change_id}` | Remove. |
| `GET /comparisons/{id}/export/{kind}` | `changes-report.docx` · `old-highlighted.pdf` · `new-highlighted.pdf` · `side-by-side.pdf` · `bundle.zip` (query `parts=` selects zip members). PDF kinds require `render_status=completed`, else 409 with a human-readable detail. |
| `POST /comparisons/{id}/rerun` | Adjust Comparison: multipart like create — optional replacement `old_file`/`new_file`/text, plus `swap=true`. Recomputes diff + render **in place** (same id) and **deletes all annotations** for the comparison (change ids are not stable across runs; the UI warns before submitting). 409 while `render_status="processing"`. |

### 3.2 Render orchestrator — `backend/app/services/render_orchestrator.py`

`run_render(comparison_id)` (executed via FastAPI `BackgroundTasks`):
1. Load row; both `content_type == "pdf"`? else → `skipped`.
2. Per side: `render_pages(pdf, out_dir, settings.pixel_render_page_cap)` →
   `positioned_words(pdf)`.
3. `word_level_ops(old_texts, new_texts)` → marks + changes; map marks onto page boxes
   (`{x0,y0,x1,y1,type,change_id}` per page).
4. Write `render_result` JSONB `{old:{pages[]}, new:{pages[]}, changes[], truncated_pages}`
   (shape already typed in `frontend/lib/types.ts:206-227`), `render_status="completed"`.
5. Any exception → `render_status="failed"`, `render_error=str(e)`, log. Never raises.
6. `DELETE /comparisons/{id}` also removes `upload_dir/renders/{id}/`.

`pdf_render_service.to_pdf` loses its Gotenberg import (PDF passthrough or raise → caller
maps to `skipped`).

### 3.3 Move detection (diff engine post-pass)

In `comparison_service`: after block/ops assembly, pair `delete` runs with `insert` runs
whose normalized token join is identical and ≥ 5 tokens. Pairs become kind `moved`
(`moved_from`/`moved_to` counterpart refs) in both `diff_result` blocks and
`word_level_ops` changes. Frontend `Enable Moves` toggle only changes **display** (moved
pair rendered dimmed/purple vs plain removed+added) — never a recompute.

### 3.4 Annotations — migration `0020`

Table `comparison_annotations`: `id` UUID PK · `comparison_id` FK → `document_comparisons`
(CASCADE) · `change_id` text · `note` text NULL · `tags` text[] default `{}` ·
`created_by` FK users NULL · `created_at`/`updated_at`. Unique `(comparison_id, change_id)`.

### 3.5 Exports — `backend/app/services/export_service.py`

- **changes-report.docx** (`python-docx`, already a dep): title/meta, counts by kind, table
  (n°, kind, old text, new text, note, tags). Works for every comparison incl. text-only.
- **old/new-highlighted.pdf**: burn that side's overlay boxes into its rendered PNGs
  (Pillow), assemble via `img2pdf`.
- **side-by-side.pdf**: per sheet, old+new page PNGs composited side by side (Pillow
  canvas), `img2pdf`.
- **bundle.zip**: stdlib `zipfile` over selected artifacts + original inputs.
- New pip deps: `img2pdf`, `Pillow` (pin explicitly; currently transitive).
- Generation is synchronous in-request (seconds at 144 DPI/60-page cap); streamed response
  with `Content-Disposition`.

## 4. Frontend

### 4.1 Module — `frontend/components/compare-viewer/`

| Unit | Responsibility |
|---|---|
| `ViewerShell` | Client orchestrator: initial data, render polling (2 s → 5 s backoff after 60 s, stops on terminal status), top-level layout grid. |
| `ViewerContext` | `selectedChangeId`, `viewMode` (`pixel`\|`text`, auto-fallback), `layout` (`side-by-side`\|`single`), per-pane zoom, `scrollLock`, `showMoves`, filters, search state. Two-context app pattern preserved — no store lib. |
| `Toolbar` | Open (→ `/compare`), Print, **Export** popover, Email (`mailto:` + URL), **Adjust Comparison** popover, Side-by-Side/Single (Single shows the **Revised** side by default with an Original/Revised switcher; heat strip + panel remain), Scroll-Page/Select-Text cursor mode (pixel view pan vs native selection), Scroll Lock, Changes Settings (Enable Moves), Prev/Next Change. |
| `PagePane` | One side: file header, per-pane search box (backend hits → outline boxes + hit stepper), `N of M` page jump, zoom −/+/preset (CSS transform), lazy `<img>` pages + overlay boxes, jump chevrons. Scroll lock syncs proportional scrollTop across panes. |
| `TextRedline` | Wraps existing `DiffViewer` for text mode/fallback; unchanged block schema. |
| `HeatStrip` | Vertical minimap between panes: red/blue (purple = moved) bars positioned by page+bbox fraction (pixel) or block index fraction (text); viewport band; click-to-jump. |
| `ChangesPanel` | Virtualized list (`@tanstack/react-virtual`), filter chips (kind + has-note/has-tag), Details toggle, footer `Change N of M` + prev/next. |
| `ChangeCard` | Kind label + n°, −N/+N word counts, red/blue text preview, tag picker, debounced note editor (optimistic, rollback toast). |
| `ExportPopover` | Artifact checklist w/ availability (render-dependent rows disabled for non-PDF), zip toggle, per-item download spinner. |
| `AdjustComparisonPopover` | Original/Modified slots, swap ⇅, replace file, Compare → `POST rerun`, then reload. |

### 4.2 Contracts

- **Selection id space**: pixel changes `r{n}` (backend), text blocks `b{index}` — one
  `selectedChangeId` for viewer ⇄ heat strip ⇄ panel; annotations key on these ids
  (text-mode ids only stable per diff run — acceptable: rerun clears annotations with a
  UI warning on the Adjust popover).
- `lib/api.ts` additions: `searchComparison`, `upsertAnnotation`, `deleteAnnotation`,
  `exportComparisonUrl(kind)`, `rerunComparison`. `lib/types.ts`: `Annotation`,
  `SearchHit`, `moved` change kind, `DocumentComparison.annotations`.
- Severity tokens stay semantic: `sev-critical` (removed), `success` (added), `primary`
  (selection), new `moved` uses the existing purple/violet token family.

## 5. Error handling

- Render failure → toolbar caption (as today) + automatic text fallback; viewer never
  dead-ends.
- Page image 404/load error → placeholder tile with retry.
- Text-only comparison → pixel toggle disabled with tooltip; exports partially disabled.
- Annotation save failure → optimistic rollback + sonner toast.
- `rerun` conflict (409) → toast "render in progress".
- Search on non-PDF side → input disabled with tooltip.

## 6. Testing

- **Backend (pytest)**: move-detection unit tests (pairing, threshold, no false pairs);
  orchestrator end-to-end on two small fixture PDFs (status transitions, box sanity,
  truncation); annotations CRUD + cascade delete; export smoke (docx parses, PDFs
  non-empty page counts, zip members); search endpoint hits/caps/409.
- **Frontend**: strict `tsc` + production build green (repo gate). Component logic kept
  pure where testable (heat-strip fraction math, filter reducers as plain functions).
- **Manual E2E checklist** (ships with the implementation plan): PDF+PDF pixel flow,
  DOCX+PDF fallback flow, 1k-change virtualization, notes/tags persistence, every export,
  rerun/swap, new-tab open, RBAC (viewer 401 without session).

## 7. Deliberate deviations from the desktop app

| Desktop | This clone | Why |
|---|---|---|
| In-app tab bar | One browser tab per comparison | Web-native; `target="_blank"` from the list |
| Save button | — | Server persists everything |
| Email attachment | `mailto:` with viewer URL | No SMTP infra |
| DOCX page rendering | Deferred (PDF-only) | No-new-containers constraint; Gotenberg seam kept |
| PDF Markup mode | Out of scope | Annotation-burned exports cover the need |

## 8. Rollout

1. Backend render wiring + serialization (unblocks existing frontend polling immediately).
2. Migration 0020 + annotations API.
3. Move detection.
4. Viewer module + route move (feature-complete UI on text mode first, pixel enrichment).
5. Search + exports.
6. Docs: update OKF pages (`okf/services/comparison-service.md` is stale re: paragraph
   algorithm; add render orchestrator + viewer pages) and `docs/DOCUMENT-COMPARISON.md` §6.
