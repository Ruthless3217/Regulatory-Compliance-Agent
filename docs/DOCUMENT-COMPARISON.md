# Document Comparison — Feature Guide

> Written to support a UI rework of the Compare feature and to identify patterns worth
> copying from (and into) the rest of the app. Sources: the OKF bundle (`okf/`) plus a
> direct read of the code on `main` (2026-07-10).

The standalone **Compare** tool diffs two versions of a document (paste / PDF / DOCX / TXT)
and shows a word-level, side-by-side redline. **Pure text processing — no LLM calls** — so
comparisons run synchronously in the request and are not guarded by the rate-limit/budget
dependencies that protect paid endpoints.

---

## 1. End-to-end flow

```
/compare/new  ──POST /comparisons (multipart)──▶  comparisons.py
                                                    │ _persist_upload() → uploads/ dir
                                                    │ extract_segments() per side
                                                    │ build_diff() → diff_result (JSONB)
                                                    │ status: completed | failed
                                                    ▼
/compare/[id] ◀──GET /comparisons/{id}──  document_comparisons row
      │
      ├─ CompareWorkspace (client)
      │    ├─ DiffViewer        ← "Text view" (always works)
      │    ├─ PixelDiffViewer   ← "Document view" (⚠ backend not wired — see §6)
      │    └─ ChangesPane       ← filterable change list, synced selection
```

## 2. Backend

### 2.1 Route — `backend/app/api/routes/comparisons.py`

| Endpoint | Notes |
|---|---|
| `POST /comparisons` | Multipart form: `title`, `old_content`/`new_content` (paste) or `old_file`/`new_file` (upload). Diff computed **inline, synchronously**; row persisted either way (`completed` or `failed` + `error_message`). Returns the full diff. |
| `GET /comparisons` | Paginated list (`skip`/`limit`), newest first, **without** diff payload. |
| `GET /comparisons/{id}` | Single row **with** `diff_result`. |
| `DELETE /comparisons/{id}` | Removes the row and any uploaded files on disk. |

All four require the `comparison:use` permission via `Depends(require(...))` (auth/RBAC added in
commit `39558d6`). Upload handling details worth preserving in any rework:

- `_detect_content_type` trusts the browser MIME first, then **falls back to the file
  extension** — Windows machines without Office send `application/octet-stream` for `.docx`.
- Files stream to `settings.upload_dir` in 8 KB chunks with a `settings.max_upload_size`
  cap (413 on overflow); extensions are sanitized to alphanumerics.

### 2.2 Diff engine — `backend/app/services/comparison_service.py`

The heart of the feature, built to survive **cross-format comparison** (e.g. DOCX draft vs
finalized PDF), where naive paragraph diffs mark the whole document changed:

1. **Extraction → sentence segments** (`extract_segments`)
   - DOCX: walks the body element in document order — paragraphs **and table cells**
     (incl. nested tables, de-duped merged cells). Headings/Title styles get a `## ` prefix.
   - PDF: strips repeated running headers/footers (`_detect_running_lines`, needs ≥3 pages)
     and page-number lines, de-hyphenates line wraps, unwraps physical lines, then splits
     into sentences. Raises a clear error for scanned/no-text PDFs ("Run OCR…").
   - Text/paste: blank-line paragraphs → sentences.
2. **Word-token alignment** (`build_diff`) — both sides flatten to `(display, match_key)`
   token streams; `difflib.SequenceMatcher` aligns the **normalized keys**
   (case/whitespace/heading-marker-insensitive). Template placeholders (`<field>`, `____`,
   `XXXX`) collapse to a shared wildcard, so *filling in a placeholder is not a change*
   (`_suppress_placeholder_fills`).
3. **Re-grouping into rows** — token opcodes are buffered and flushed at sentence
   boundaries into display blocks. If a replace-run has *no* unchanged tokens on either
   side, it's split into separate delete + insert blocks instead of forcing unrelated
   content to face each other.

**Block schema** (the contract the frontend renders — keep stable through any rework):

```ts
type DiffBlock =
  | { type: "equal";   old_text: string; new_text: string }
  | { type: "delete";  old_text: string }
  | { type: "insert";  new_text: string }
  | { type: "replace"; old_words: DiffWord[]; new_words: DiffWord[] };  // DiffWord = {text, changed}
```

There is also `word_level_ops(old_texts, new_texts)` — same normalizer, but aligns two
*positioned-word* streams and returns `(old_marks, new_marks, changes)` for the pixel
overlay. **Currently dead code** (§6).

> Legacy note: the older paragraph-level functions (`extract_paragraphs`,
> `extract_docx_paragraphs`, `extract_pdf_paragraphs`) still exist in the module but the
> route now uses the segment/token path. The OKF page (`okf/services/comparison-service.md`)
> still describes the old paragraph algorithm — it predates the rewrite.

### 2.3 Pixel render service — `backend/app/services/pdf_render_service.py`

Built for the "Document view" (pixel-faithful) mode:

- `to_pdf(file, type, out_dir, side)` — PDF passthrough; DOCX → PDF via the **Gotenberg**
  sidecar (`gotenberg_client.convert_to_pdf`, `settings.gotenberg_url`, default
  `http://gotenberg:3000`); text raises (nothing to render).
- `render_pages(pdf, out_dir, cap)` — pypdfium2 renders PNGs at scale 2.0 (~144 DPI), up to
  `settings.pixel_render_page_cap` (60) pages/side; returns page metas (`w_pt`/`h_pt` in PDF
  points) + truncated count.
- `positioned_words(pdf)` — pdfplumber words with bboxes, running headers/footers and page
  numbers removed with the same policy as the text extractor.

### 2.4 Data model — `backend/app/models/document_comparison.py` (`document_comparisons`)

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `title`, `old/new_content_type`, `old/new_file_path`, `old/new_original_content` | inputs |
| `diff_result` | JSONB — array of `DiffBlock` |
| `status` / `error_message` | `processing` / `completed` / `failed` |
| `render_result` | JSONB — pages + boxes + changes overlay model (migration `0017`) |
| `render_status` / `render_error` | `processing` / `completed` / `failed` / `skipped` |
| `created_by` | → `users` |

## 3. API client — `frontend/lib/api.ts`

`listComparisons`, `getComparison`, `createComparison` (manual `FormData`, `credentials:
"include"`), `deleteComparison`, and `comparisonPageImageUrl(id, side, n)` →
`GET /comparisons/{id}/pages/{side}/{n}` (an endpoint that **does not exist yet on the
backend** — §6). Types live in `frontend/lib/types.ts` (`DocumentComparison`, `DiffBlock`,
`RenderResult`, `ChangeItem`, …).

## 4. Frontend UI (the part being reworked)

### 4.1 Pages — `frontend/app/(workspace)/compare/`

| Route | File | What it is |
|---|---|---|
| `/compare` | `page.tsx` (server) | List: `PageHeader` + plain `<table>` of title / `StatusPill` / created date, empty-state card, "New comparison" hero button. `force-dynamic`. |
| `/compare/new` | `new/page.tsx` (client) | Two `SideInput` columns (Original / Revised), each a paste-textarea ⇄ file-upload tab (`.pdf,.docx,.txt`, 50 MB client cap), title input, sonner toasts, redirects to the viewer on success. **Note: the dropzone is click-only — no real drag-and-drop handlers despite the "Drop a file" copy.** |
| `/compare/[id]` | `[id]/page.tsx` (server) | Back-link + `PageHeader` (status pill, created) + failed-state banner, else `CompareWorkspace`. Wider shell (`max-w-[1400px]`) than the rest of the app. |

### 4.2 Components — `frontend/components/compare/`

**`CompareWorkspace.tsx`** — client orchestrator and the state owner:
- `selectedId: string | null` — the single selection shared by viewer ⇄ sidebar.
- `live: DocumentComparison` — polls `getComparison` every 2 s **while
  `render_status === "processing"`** (pixel render is expected to be async).
- View toggle: `"pixel" | "text"`; pixel is default but only effective when
  `render_status === "completed" && render_result` — otherwise silently falls back to text.
  Pending/failed states get a small inline caption.
- Layout: `grid lg:grid-cols-[1fr_360px]` — viewer + fixed 360 px sidebar.

**`DiffViewer.tsx`** (Text view) — two-column table ("Original" / "Revised" header row),
one `DiffRow` per block, `max-h-[70vh]` scroll region:
- delete → red `line-through` left cell; insert → green right cell; replace → per-word
  spans colored by `word.changed`; equal → plain both sides.
- Changed rows are clickable → `onSelect(String(blockIndex))`; external selection scrolls
  the row into view (`scrollIntoView` center) and pulses it via `data-pulse` for 850 ms —
  a pattern explicitly mirrored from the review page's `DocumentPane`.
- Row DOM ids: `cmp-block-{index}` (`diffRowDomId`).

**`PixelDiffViewer.tsx`** (Document view) — same header chrome; two `SideColumn`s render
each page as an `<img loading="lazy">` (URL from `comparisonPageImageUrl`) with **absolutely
positioned highlight boxes** as `%` of page points (`aspect-ratio: w_pt / h_pt` keeps the
overlay aligned at any width). Old side = red boxes, new side = green; selected box gets
`ring-primary`. Truncation banner when `truncated_pages > 0`. Box DOM ids:
`pxl-{side}-{changeId}`.

**`ChangesPane.tsx`** — the right sidebar:
- Header with word tallies: `− N words` / `+ N words` (`countDiffStats` — replace-block
  words count toward both sides).
- Filter chips All / Removed / Added / Modified with live counts.
- `ChangeCard` list (kind dot + label, `line-clamp-2` removed/added text preview);
  selection highlights the card and scrolls it into view (mirrors `ViolationsPane`).
- In text mode, changes come from `deriveChanges(blocks)` (id = block index as string;
  for `replace` only the changed words are shown). In pixel mode they come from
  `render_result.changes` (id = `r{n}` from `word_level_ops`).

### 4.3 Design-system pieces the feature leans on

Token-based HSL CSS vars (single "Bajaj blue" brand): `sev-critical` (red/removed),
`success` (green/added), `primary` / `primary-50` (selection), `muted`, `border`,
`shadow-card`, the `micro-label` utility, `font-mono` for numbers. Shared primitives:
`PageHeader`/`PageHeaderMeta`, `StatusPill`, `Button` (incl. `size="hero"`), `Tabs`,
`Input`, `Textarea`, sonner toasts. No context/store — plain `useState` lifted to
`CompareWorkspace`, which is the correct-sized state for this feature.

### 4.4 Interaction contract (preserve through any rework)

1. One selection id shared by viewer and sidebar; text mode ids are block indices,
   pixel mode ids are `r{n}` change ids.
2. Selecting in the sidebar scrolls + pulses the viewer row / scrolls to the overlay box;
   selecting in the viewer scrolls the sidebar card into view.
3. Colors are semantic tokens (`sev-critical` / `success`), not hard-coded reds/greens.
4. Text view must remain the always-available fallback when the pixel render is
   pending / failed / skipped.

## 5. Deployment note

Pixel mode adds a **Gotenberg sidecar** (LibreOffice wrapper, internal compose DNS
`gotenberg:3000`, no API key) for DOCX→PDF, plus `pypdfium2`/`pdfplumber` in the backend
image. Page images are written next to the uploads; the render cap (60 pages/side) reports
overflow as `truncated_pages` rather than failing.

## 6. ⚠ Known gap: the pixel "Document view" backend is not wired up

Everything for pixel compare exists **except the orchestration**. Verified on `main`
(`39558d6`):

| Layer | State |
|---|---|
| DB columns `render_result` / `render_status` / `render_error` (migration `0017`) | ✅ exist |
| `pdf_render_service.py` (to_pdf / render_pages / positioned_words) + `gotenberg_client.py` | ✅ exist, **never imported by any route** |
| `comparison_service.word_level_ops` (overlay diff) | ✅ exists, **never called** |
| Frontend (`PixelDiffViewer`, toggle, polling, types, `comparisonPageImageUrl`) | ✅ complete |
| `POST /comparisons` triggering a render + writing `render_*` | ❌ missing |
| `_serialize` including `render_status` / `render_result` | ❌ missing — the API never returns them |
| `GET /comparisons/{id}/pages/{side}/{n}` image endpoint | ❌ missing |

Net effect today: the frontend receives `render_status: undefined`, so `hasPixel` is false,
polling never starts, and the workspace **always falls back to Text view**. The
"Document view" button is permanently disabled. If the UI rework keeps pixel mode, plan the
backend wiring as part of it:

1. In `create_comparison`, after the text diff: if both sides are pdf/docx, kick a render
   (background task) — `to_pdf` → `render_pages` (cap from settings) → `positioned_words`
   per side → `word_level_ops` → assemble `RenderResult` (pages+boxes+changes shape in
   `frontend/lib/types.ts:206-227`) → write `render_result`/`render_status`. Text/paste
   sides → `render_status = "skipped"`.
2. Add `render_status`, `render_result` (and optionally `render_error`) to `_serialize`.
3. Add the page-image endpoint streaming the stored PNGs (auth-guard it with
   `comparison:use`; note `<img src>` sends cookies same-origin, which matches the
   session-cookie auth).

## 7. Features elsewhere in the app worth copying into Compare

Patterns already in this codebase that fit naturally into a Compare rework:

- **SSE progress instead of polling** — `lib/sse.ts` (`streamSSE`/`useSSEStream`, SSE over
  POST) powers analysis + chat streaming. A `/comparisons/{id}/stream` progress channel
  would replace the 2 s `setInterval` poll in `CompareWorkspace` and match how the Review
  tab shows live stages.
- **Review workspace layout** (`components/review/*`) — `DocumentPane`+`ViolationsPane` is
  the same document+sidebar shape as Compare; its suppressed/"needs review" lane pattern
  suggests a collapsed "placeholder fills (suppressed)" lane in `ChangesPane`, surfacing
  what `_suppress_placeholder_fills` currently hides silently.
- **Report page** (`components/report/*`) — print-only styles + PDF export. A printable
  "redline report" (summary header + change list) is a cheap, high-value copy.
- **Command palette** (`CommandPaletteProvider`, ⌘K) — register compare actions
  ("New comparison", jump to recent comparisons); next/previous-change navigation
  (`j`/`k` style) fits the existing keyboard-first pattern.
- **Submissions inbox** (`/` page) — KPI strip + grouped tables + activity rail; the
  `/compare` list page is a bare table and could adopt the same header treatment
  (e.g. counts by status, words added/removed at a glance).
- **Dashboard tiles** (`/dashboard`) — if comparisons matter operationally, a
  "recent comparisons" tile is trivial via the existing list endpoint.
- **`similar` router pattern** — `GET /submissions/{id}/similar` shows how a follow-on
  "compare this submission against a previous version" entry point could hang off an
  existing entity rather than only standalone uploads.

## 8. File index

| Concern | Path |
|---|---|
| Routes (list/new/viewer) | `frontend/app/(workspace)/compare/{page,new/page,[id]/page}.tsx` |
| Workspace + toggle + polling | `frontend/components/compare/CompareWorkspace.tsx` |
| Text diff viewer | `frontend/components/compare/DiffViewer.tsx` |
| Pixel viewer | `frontend/components/compare/PixelDiffViewer.tsx` |
| Changes sidebar | `frontend/components/compare/ChangesPane.tsx` |
| Change derivation + word tallies | `frontend/lib/format.ts` (`deriveChanges`, `countDiffStats`) |
| API client + page-image URL | `frontend/lib/api.ts` (comparisons section) |
| Types | `frontend/lib/types.ts` (`DiffBlock` … `DocumentComparison`) |
| REST routes | `backend/app/api/routes/comparisons.py` |
| Diff engine | `backend/app/services/comparison_service.py` |
| Pixel render service | `backend/app/services/pdf_render_service.py` |
| Gotenberg client | `backend/app/services/gotenberg_client.py` |
| Model / migrations | `backend/app/models/document_comparison.py` · `alembic/versions/0013`, `0017` |
| Config | `backend/app/config.py` (`gotenberg_url`, `pixel_render_page_cap`, upload settings) |
| OKF pages (higher-level, partly dated) | `okf/services/comparison-service.md` · `okf/data-model/document-comparisons.md` · `okf/frontend/routing.md` |
