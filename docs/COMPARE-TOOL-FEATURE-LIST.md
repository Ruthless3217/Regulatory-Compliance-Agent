# Compare Tool — Feature List (from screen recording)

> Extracted from `Screen Recording 2026-07-10 163437.mp4` (95 s, 48 analyzed frames — kept in
> `Desktop\compare-app-frames\`). The app is a Draftable-Desktop-style document comparison
> tool, shown comparing a 27-page DOCX (policy template) against a 28-page PDF (issued
> policy). This is the feature inventory for cloning it into our Compare rework — see
> `docs/DOCUMENT-COMPARISON.md` §4/§6 for what we already have.

## 1. App shell

| Feature | Detail |
|---|---|
| Tabbed workspace | Title-bar tab per comparison ("Bajaj Life GAIN - Ann H - Policy…") + `+` to start another comparison in a new tab |
| Window chrome | Settings gear + Help (?) top-right |
| Right icon rail | Toggle buttons for the Changes panel and an export/side-panel |
| Collapsible toolbar | Chevron to collapse the main toolbar |

## 2. Main toolbar

| Feature | Detail |
|---|---|
| **Open** | Load files / saved comparison |
| **Save** | Persist the comparison |
| **Print** | Print output |
| **Copy to Clipboard** | Opens the **export popover** (see §7) |
| **Email** | Same export set, sent via email |
| **Adjust Comparison** | Popover: **Reload from Source**, ORIGINAL / MODIFIED file slots with a **swap (⇅)** button between them, **Reset** link, **Compare ▾** split-button to re-run |
| **Side By Side / Single** | View-mode toggle — two panes vs one full-width document (Changes panel + heat strip remain in Single) |
| **Scroll Page / Select Text** | Mutually exclusive cursor modes: pan-to-scroll (hand) vs text selection (I-beam) |
| **Scroll Lock** | Toggle synchronized scrolling of the two panes |
| **Changes Settings** | Dropdown: **Moves** section with ⓘ tooltip + **Enable Moves** checkbox (move detection on/off) |
| **PDF Markup** | Highlighted/premium toolbar item — annotation/markup output mode |
| **Previous / Next Change** | Global change-to-change navigation |

## 3. Per-pane controls (duplicated left & right)

| Feature | Detail |
|---|---|
| In-document search | Magnifier per pane |
| Page navigation | Up/down arrows + editable `N of M` page box, independent per side (27 vs 28 pages) |
| Zoom | − / + buttons and a zoom preset dropdown ("Automatic Zoom") per pane |
| File header | File-type icon (DOCX/PDF) + full filename above each pane |
| Jump chevrons | Floating circular scroll-to-prev/next buttons overlaid on each pane |
| Async page render | Pages lazy-render with spinners; UI stays responsive |

## 4. Center change map ("heat strip")

- Full-height vertical strip between the panes: one thin bar per change, **red = removed
  (left doc), blue = inserted (right doc)**, position proportional to document location.
- Darker band indicates the current viewport; instantly communicates change density
  (this recording: 1,006 changes).
- Serves as a minimap/scrollbar for jumping to hot areas.

## 5. In-document rendering & interaction

- **Pixel-faithful page rendering** of both DOCX and PDF (original layout, tables, QR
  codes, images), not re-flowed text.
- Word-level inline highlights painted **on** the rendered page: pink/red = deleted,
  blue = inserted.
- Case-level sensitivity ("GAIN"→"Gain", "f"→"F" reported as REPLACED).
- Hovering a highlight shows a **tooltip naming the change type** ("Inserted").
- Clicking a change draws a **selection box around the corresponding region in BOTH
  panes** (linked highlight), with a floating copy-text button beside it.
- Tooltips on toolbar/page controls ("Next Page").

## 6. Changes panel (right sidebar)

| Feature | Detail |
|---|---|
| Header | "Changes" + **Select** mode + **Settings** |
| Filters | "Filters (1)" row with funnel icon — filterable change list; **Details ›** toggle for expanded card info |
| Change cards | Numbered + typed (`1. INSERTED`, `2. REPLACED`…), old text in red / new text in blue, per-card **−N / +N word counts**, per-card **tag icon** (labeling) |
| Notes | Selected card expands an **"Add a Note"** free-text input — reviewer annotations attached to a specific change |
| Navigation | Footer: total (**"1006 changes"** → **"Change 2 of 1006"** once selected) with prev/next arrows; card click ⇄ document scroll sync both ways |

## 7. Export popover (Copy to Clipboard / Email / Save)

**Comparison Results** — each row has a rename (pencil) + include checkbox:
1. **Side By Side** (PDF) — the two-pane redline view as a document
2. **Single Document** (PDF)
3. **Older file (with highlights)** (PDF) — original with deletion highlights baked in
4. **Newer file (with highlights)** (PDF)
5. **Changes Report** (DOCX) — the change list as a report

**Comparison Inputs** — the raw **Older file** / **Newer file** as-uploaded.

Plus: **Zip files** checkbox, **Copy** primary action, per-item async generation
(spinners while each artifact builds), "Learn more ⓘ".

## 8. Comparison engine capabilities (implied)

- **Cross-format**: DOCX template vs issued PDF compared cleanly despite different
  pagination (27 vs 28 pages) — template placeholders vs filled values are visible as
  ordinary REPLACED changes.
- **Move detection** (opt-in via Enable Moves).
- **Scale**: 1,006 changes across ~28 pages with responsive UI.
- Word-token granularity with per-change word counts.

---

## 9. Gap map vs our Compare feature (`/compare`)

| Cloned-app feature | Our status today |
|---|---|
| Side-by-side text redline, word-level | ✅ `DiffViewer` |
| Changes sidebar w/ counts + filter | ✅ `ChangesPane` (All/Removed/Added/Modified) |
| Pixel-faithful page view + overlay boxes | 🟡 Frontend built (`PixelDiffViewer`); backend orchestration missing (see DOCUMENT-COMPARISON.md §6) |
| Placeholder-aware cross-format diff | ✅ backend (`_suppress_placeholder_fills`) — but suppressed fills are invisible in the UI |
| Prev/Next change navigation | ❌ sidebar click only — no ordered stepping, no keyboard |
| Center heat strip / change minimap | ❌ |
| Change counter ("Change N of M") | ❌ |
| Notes on a change | ❌ |
| Tags/labels on a change | ❌ |
| Filters beyond kind (page, severity…) | ❌ |
| Synchronized scrolling / scroll lock | ❌ (single scroll container in text view; pixel view scrolls both columns together implicitly) |
| Per-pane zoom & page jump | ❌ |
| In-document search | ❌ |
| Export: side-by-side PDF / highlighted files / changes report | ❌ (no export at all) |
| Single-document view mode | ❌ |
| Move detection | ❌ |
| Adjust/re-run comparison (swap sides, replace a file) | ❌ (comparisons are immutable once created) |
| Tabbed multi-comparison workspace | ❌ (list page instead — arguably fine for web) |
| Email / Print | ❌ (browser print exists trivially; report pattern from `components/report/*` can be copied) |

### Suggested clone order (highest value ÷ effort first)
1. **Prev/Next change stepping + "Change N of M" footer** — pure frontend, extends the existing selection contract.
2. **Heat strip minimap** — derive from `diff_result` block positions; works in both view modes.
3. **Wire the pixel view backend** (already specced in DOCUMENT-COMPARISON.md §6) — unlocks the layout-faithful view that defines this product.
4. **Export pack** (side-by-side PDF + changes report) — copy the Report page's print/PDF pattern.
5. **Change notes + tags** — needs a small backend addition (JSONB annotations column or table).
6. **Suppressed-fills lane** — surface placeholder fills the engine already detects.
7. **Scroll lock + per-pane zoom/page-jump** — pixel-view polish.
8. **Move detection** — backend diff enhancement (difflib doesn't do moves natively; needs a post-pass).
