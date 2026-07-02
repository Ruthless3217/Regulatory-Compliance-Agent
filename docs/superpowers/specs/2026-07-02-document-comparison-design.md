# Document Comparison ("Compare") — Design

**Date:** 2026-07-02
**Status:** Approved (pending spec review)
**Author:** AI marketing team + Claude
**Branch:** `feature/document-comparison`

## Problem

Marketers iterate on marketing copy across multiple drafts (e.g. brochure v1 → v2, legal-reviewed redline → final). Today there is no way in this tool to see, at a glance, what changed between two versions of a document — reviewers either eyeball two files side by side manually or rely on Word's own track-changes (only available if the author remembered to turn it on). This mirrors what draftable.com does for general document redlining, but scoped to this app's users and file types.

## Goal

Let a user upload two versions of a document (or paste two blocks of text) and get a word-level, side-by-side redline view of what was added, removed, or changed — independent of compliance scoring.

### Non-goals (YAGNI)

- **No integration with compliance analysis.** This is a standalone tool. It does not re-run violation scoring, does not touch `Submission`/`ComplianceCheck`, and shares no runtime code path with `compliance.py`. (Revisit only if a future need explicitly asks for "compare + re-score.")
- **No move/relocation detection.** A paragraph moved from position 3 to position 7 is shown as a delete at position 3 and an insert at position 7, not linked as "moved." Simpler diff algorithm; revisit only if users find this confusing in practice.
- **No visual/formatting fidelity.** The diff view shows extracted text in a clean uniform reading pane — no attempt to reproduce original fonts, tables, images, or page layout. This matches what actually matters for a wording review.
- **No async/streaming processing.** Diff computation is pure text processing (no LLM calls), so `POST /comparisons` is synchronous request/response — no job polling infrastructure like compliance analysis needs.

## Design

### Flow

1. User opens the new "Compare" section, uploads two files (DOCX/PDF) or pastes text for "Original" and "Revised", with an optional title.
2. Backend saves any uploaded files (reusing the existing upload-dir pattern), extracts each side into a paragraph list, aligns paragraphs between the two versions, diffs words within changed paragraphs, and persists both the source files/text and the computed diff result.
3. Frontend renders a synchronized side-by-side view: left pane = original (deletions struck through/red), right pane = revised (insertions highlighted/green).
4. A "Compare" history list (mirroring the Submissions list) lets the user revisit past comparisons without recomputation.

### Extraction

New paragraph-level extraction in `comparison_service.py`, adapted from `preprocessing_service.py`'s existing `_extract_docx`/`_extract_pdf` (same libraries — `python-docx`, `pdfplumber`) but returning `list[str]` (one entry per paragraph/page-block) instead of a single joined string. Headings are still tagged (`## `) so they can render distinctly. Pasted text is split into paragraphs on blank lines.

### Diff algorithm

Two-pass, stdlib-only (`difflib.SequenceMatcher`), no new dependencies:

1. **Paragraph alignment:** `SequenceMatcher(None, old_paragraphs, new_paragraphs)` over the paragraph lists produces opcodes (`equal`, `replace`, `delete`, `insert`).
2. **Word diff:** for each `replace` opcode, a second `SequenceMatcher` over the two paragraphs' whitespace-tokenized word lists produces inserted/deleted/equal word spans.

`diff_result` (stored as JSON) is an ordered list of blocks:

```json
[
  {"type": "equal", "old_text": "...", "new_text": "..."},
  {"type": "delete", "old_text": "..."},
  {"type": "insert", "new_text": "..."},
  {"type": "replace",
   "old_words": [{"text": "...", "changed": true}, ...],
   "new_words": [{"text": "...", "changed": true}, ...]}
]
```

The frontend renders these blocks directly with no client-side diff recomputation. `equal`/`replace` blocks render at the same row in both panes; `delete` renders left-only, `insert` renders right-only — this keeps the two panes roughly scroll-aligned without JS scroll-position math.

### Data model

New table `document_comparisons` (mirrors `Submission`'s shape in `backend/app/models/submission.py`):

```python
class DocumentComparison(Base):
    __tablename__ = "document_comparisons"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    old_content_type = Column(String(50), nullable=False)   # docx, pdf, text
    new_content_type = Column(String(50), nullable=False)
    old_file_path = Column(String(1000), nullable=True)     # null if pasted text
    new_file_path = Column(String(1000), nullable=True)
    old_original_content = Column(Text, nullable=True)      # pasted text, if used
    new_original_content = Column(Text, nullable=True)
    diff_result = Column(JSON, nullable=True)                # null until status=completed
    status = Column(String(50), default="processing")        # processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

New Alembic migration adds this table.

### API

New router `backend/app/api/routes/comparisons.py`, prefix `/comparisons`, registered alongside the existing routers in `backend/app/api/routes/__init__.py`:

- `POST /comparisons` — multipart form: `title`, `old_content_type`/`new_content_type`, `old_file`/`new_file` (optional `UploadFile`s, same `ALLOWED_CONTENT_TYPES` map and `settings.max_upload_size` limit as `submissions.py`), `old_content`/`new_content` (optional pasted text). Saves files, runs extraction + diff inline, persists and returns the full record including `diff_result`.
- `GET /comparisons` — paginated list (`id`, `title`, `status`, `created_at`) for the history page. Same `skip`/`limit` query-param pattern as `GET /submissions`.
- `GET /comparisons/{id}` — full record including `diff_result`, for the detail/viewer page.
- `DELETE /comparisons/{id}` — deletes the DB row and any saved files on disk.

### Error handling

- Extraction failure on either side (corrupt DOCX, unparseable PDF) → row saved with `status="failed"` and `error_message` set. `POST /comparisons` still returns 200 with the failed-status record (not a 500), so the frontend shows an inline error instead of crashing.
- A document that extracts to zero paragraphs is valid, not an error — it produces a diff where every paragraph on the other side is a pure insert/delete.
- Identical content on both sides is valid — produces an all-`equal` diff ("no changes detected").
- File type/size validation reuses `submissions.py`'s existing `ALLOWED_CONTENT_TYPES` map and `settings.max_upload_size`.

### Frontend

New route group `frontend/app/(workspace)/compare/`:

- `compare/page.tsx` — history list, mirrors `submissions/page.tsx`: table of past comparisons + "+ New comparison" button.
- `compare/new/page.tsx` — upload form: two file/paste inputs labeled "Original"/"Revised", optional title, submit → `POST /comparisons` → redirect to the detail view.
- `compare/[id]/page.tsx` — side-by-side diff viewer: two scrollable panes rendering `diff_result` blocks, synced scroll (via shared block index), legend (red = removed, green = added).

New components under `frontend/components/compare/`, following existing patterns in `components/ui` and `components/submissions`-equivalent structure. New "Compare" item added to the top-bar nav alongside Dashboard/Submissions/Knowledge Base/Rules.

## Components & touchpoints

| Unit | File | Responsibility |
|---|---|---|
| `DocumentComparison` model | `backend/app/models/document_comparison.py` (new) | ORM model for persisted comparisons |
| Alembic migration | `backend/alembic/versions/` (new) | Creates `document_comparisons` table |
| `comparison_service.py` | `backend/app/services/comparison_service.py` (new) | Paragraph extraction (docx/pdf/text), paragraph alignment, word diff — pure functions, no I/O beyond file reads |
| `comparisons.py` routes | `backend/app/api/routes/comparisons.py` (new) | POST/GET/DELETE endpoints, file save (mirrors `submissions.py`) |
| Router registration | `backend/app/api/routes/__init__.py` | Wire up new router |
| Compare pages | `frontend/app/(workspace)/compare/**` (new) | History list, upload form, side-by-side viewer |
| Compare components | `frontend/components/compare/**` (new) | Diff block renderer, upload form, history table |
| Top-bar nav | `frontend/components/workspace/*` (existing nav component) | Add "Compare" nav item |

## Testing

TDD, per project convention:

- `backend/tests/services/test_comparison_service.py` — paragraph extraction (docx/pdf/text fixtures), paragraph alignment, word-diff correctness for pure insert, pure delete, replace, no-change, and empty-document cases.
- `backend/tests/api/test_comparisons_routes.py` — POST/GET/DELETE behavior, failed-extraction status path, pagination, file-size/type validation reuse.
- Frontend: render tests for each diff-block type (`equal`/`insert`/`delete`/`replace`) in the viewer component.

## Rollout

Implemented on `feature/document-comparison`, merged to `main` via PR once implementation + tests are complete and reviewed. No feature flag needed (fully standalone, additive-only — new table, new routes, new pages; touches no existing code paths).
