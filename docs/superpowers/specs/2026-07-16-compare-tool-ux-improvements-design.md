# Compare tool UX improvements

Date: 2026-07-16

## Context

Three usability gaps reported in the Compare tool's "New comparison" creation flow and viewer:

1. The upload dropzone on `/compare/new` visually looks like a drag-and-drop target ("Drop a file or click to browse…") but only supports click-to-browse — dropping a file does nothing.
2. Submitting a new comparison shows only a text change ("Compare →" → "Comparing…") on the button, with no spinner or progress indication, even though large PDF diffing can take several seconds.
3. The comparison viewer always labels the two documents "Original" and "Revised", even when the user uploaded named files (e.g. "brochure-v1.pdf" vs "brochure-v2.pdf") — there's no way to tell which physical file is which at a glance.
4. Once a file is selected on the upload side of the form, replacing it takes two clicks: "Choose a different file" only clears the selection, then a second click on the now-empty box is needed to actually reopen the file picker.

Scope for this spec: the "New comparison" creation page (`compare/new/page.tsx`) and the read-only viewer components. The "Adjust" (re-run) popover on the viewer (`AdjustComparisonPopover.tsx`) is explicitly out of scope for drag-and-drop and the loader — its upload slots stay click-only, and its "Original"/"Modified" labels stay as-is since they describe *which side to replace*, not a display of an already-known file.

## 1. Drag-and-drop upload

**File:** `frontend/app/(workspace)/compare/new/page.tsx`, `SideInput` component.

- Add `onDragOver` (preventDefault, set a local `isDragging` state, apply a highlighted border/background), `onDragLeave` (clear `isDragging`), and `onDrop` (preventDefault, clear `isDragging`, read `e.dataTransfer.files[0]`).
- Extract the existing validation logic (size limit check + toast) out of the file-input's `onChange` into a single `handleFile(f: File)` function, called from both `onChange` and `onDrop` — no duplicated validation.
- Add an extension check to `handleFile`: reject files whose extension isn't `.pdf`/`.docx`/`.txt` with a toast error (`"Unsupported file type. Use PDF, DOCX, or TXT."`). The native `accept` attribute only constrains the OS file picker, not drag-and-drop, so this check is currently missing entirely for dropped files.
- Purely additive to the existing dropzone markup — no layout changes when not dragging.

**Change-file fix (paper cut in the same component):** today, once a file is selected, clicking "Choose a different file" only clears the selection (`setFile(null)`, via `e.stopPropagation()`) — the user then has to click again on the now-empty box to actually open the picker. Fix:
- The selected-file state's "Choose a different file" link becomes "Change file" and, instead of clearing, directly calls `fileInputRef.current?.click()` to reopen the OS picker in one click; picking a file overwrites the current selection via the existing `onChange` → `handleFile` path.
- Add a small separate icon-only clear/remove control (e.g. an `X` button, `lucide-react`'s `X`) next to the filename that calls `setFile(null)` — so emptying the slot without immediately picking a replacement is still possible, just via a distinct control from "Change file".

## 2. Loader / progress bar on submit

**File:** `frontend/app/(workspace)/compare/new/page.tsx`.

No backend changes. `POST /comparisons` remains a single synchronous call — there is no real progress signal to report, so this is a frontend perceived-progress treatment only:

- Replace the plain "Comparing…" button text with the `Loader2` spinner icon (lucide-react), matching the existing pattern in `AdjustComparisonPopover.tsx`.
- Add a slim indeterminate animated progress bar (CSS keyframe stripe, no real percentage) rendered under the form while `submitting` is true.
- Cycle a status line on a timer for perceived progress:
  - `0ms`: "Uploading documents…"
  - `~1500ms`: "Extracting text…"
  - `~4000ms`: "Comparing changes…"
  
  These are cosmetic pacing hints based on elapsed time, not real backend phase signals. On an unusually large document the text may sit on "Comparing changes…" for a while before the request actually resolves — acceptable per user sign-off, not worth the backend rework (converting comparison creation into a background job + polling) to fix.
- Timer cleared on unmount / on request completion (success or error).

## 3. Show filenames instead of "Original"/"Revised" in the viewer

### Backend

- New Alembic migration `0021_comparison_filenames.py` (`down_revision = "0020"`): add nullable `old_filename` (`String(500)`) and `new_filename` (`String(500)`) columns to `document_comparisons`.
- `backend/app/models/document_comparison.py`: add the two new columns.
- `backend/app/api/routes/comparisons.py`:
  - `create_comparison`: set `old_filename=old_file.filename` / `new_filename=new_file.filename` when a file is uploaded (mirrors the existing `old_file_path`/`new_file_path` population); leave `None` when that side is pasted text.
  - `rerun_comparison`: same population when a replacement file is uploaded; explicitly clear back to `None` in the branch that already clears `old_file_path`/`new_file_path` when a side switches from file to pasted text, so a stale filename never survives a rerun that removes the file.
  - `_serialize()`: include `old_filename` and `new_filename` in the returned dict.

### Frontend

- `frontend/lib/types.ts`: add `old_filename?: string | null` and `new_filename?: string | null` to `DocumentComparison`.
- `frontend/lib/format.ts`: add `sideLabel(c: DocumentComparison, side: "old" | "new"): string` returning the filename if present, else `"Original"` (old) / `"Revised"` (new).
- Replace hardcoded "Original"/"Revised" text with `sideLabel(...)` in:
  - `components/compare/DiffViewer.tsx` (pane headers)
  - `components/compare/PixelDiffViewer.tsx` (pane headers)
  - `components/compare-viewer/PagePane.tsx` (`genericName` becomes the fallback value passed through the same helper, rather than a locally hardcoded string)
  - `components/compare-viewer/Toolbar.tsx` (side-toggle buttons)
- **Left untouched:** `compare/new/page.tsx`'s "Original"/"Revised" input-side labels (creation-time instructions, not a display of a known file) and `AdjustComparisonPopover.tsx`'s "Original"/"Modified" slot headers (label *which side to replace*, not an existing file's name).

## Testing

- Manual verification via the `verify` skill / browser: drag a PDF onto each dropzone and confirm it's accepted the same as click-to-browse; drag a `.png` and confirm the rejection toast; submit a comparison and observe the spinner + progress bar + staged text; open a comparison created from two named file uploads and confirm the viewer panes/toggle show the real filenames instead of "Original"/"Revised"; create one from pasted text and confirm the fallback labels still appear.
- No new automated test infra exists for this frontend (no test runner found in this pass) — verification is manual/browser-driven per the `verify` skill.
