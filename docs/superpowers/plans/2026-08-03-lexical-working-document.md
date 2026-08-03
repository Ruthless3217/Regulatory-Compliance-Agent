# Lexical Working Document — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Lexical editor state the working source of truth for an uploaded document — imported from the original DOCX, edited richly, and exported back to DOCX/PDF — while the uploaded file stays immutable.

**Architecture:** The uploaded file is never mutated. On upload it is converted once into Lexical JSON (`mammoth` DOCX→HTML, then Lexical's `$generateNodesFromDOM`), stored in a new `lexical_state` JSONB column, and edited in a Lexical editor. Export runs the reverse (`$generateHtmlFromNodes`, then the `docx` package) server-side so exports do not depend on a browser. Compliance findings are untouched in this phase and continue to work off extracted text; re-anchoring them to Lexical nodes is Phase 2.

**Tech Stack:** Next.js 15.0.3, React 19, TypeScript 5.7, Lexical 0.48, `mammoth` (DOCX→HTML), `docx` (HTML→DOCX), FastAPI, SQLAlchemy, Alembic (current head revision id `0032`, file `0032_widen_violation_category.py`), pytest.

## Global Constraints

- The uploaded file at `submissions.file_path` is **immutable**. No task may write to it.
- Three artifacts, always distinguishable: **original upload** (immutable), **working document** (`lexical_state`), **approved output** (generated export).
- Existing submissions must keep working. Every code path must tolerate `lexical_state IS NULL` and fall back to today's text behaviour.
- Alembic migrations run only inside the backend container (`RAG_EMBEDDING_DIM` must be set); never from a host shell. See `DEPLOY.md` §2.
- Backend verification: `cd backend && python -m pytest -q`. Frontend: `cd frontend && npx tsc --noEmit && npx next build --no-lint`.
- New frontend dependencies must be added to `frontend/package.json` explicitly and justified in the commit message.

## What this plan obsoletes

`submission_export_service._edited_original_docx` (added 2026-08-03, commit `36029dc`) writes accepted edits back into the uploaded DOCX. Under this architecture the original is never edited and export is generated from `lexical_state`, so that function and `tests/test_clean_docx_preserves_formatting.py` become dead once Task 6 lands. Task 6 removes them deliberately rather than leaving two competing export paths. The plain-text rebuild stays as the fallback for submissions with no `lexical_state`.

## Phase decomposition

This plan is **Phase 1 only**. Each later phase gets its own plan and produces working software on its own:

- **Phase 1 (this plan):** import → edit → export round-trip. Findings unchanged.
- **Phase 2:** re-anchor findings to Lexical (node key + offsets + text snapshot + surrounding fingerprint), replacing `highlightMarkup.ts` offset anchoring and `export_common.find_spans`.
- **Phase 3:** AI editing surface — slash commands, inline autocomplete, rewrite/shorten/expand/formalize/simplify.
- **Phase 4:** scoped re-runs (paragraph / section / changed-only / full) and old-vs-new finding comparison.
- **Phase 5:** revision audit fields — actor, manual vs AI, related rule, reviewer comment, score before/after.

## File Structure

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0033_lexical_state.py` | Adds `lexical_state` (JSONB) and `lexical_html` (Text) to `submissions` and `submission_revisions` |
| `backend/app/services/lexical_import.py` | DOCX bytes → structured HTML. Pure, no DB |
| `backend/app/services/lexical_document_service.py` | Binds a submission to its import HTML. Reads the upload, never writes it |
| `backend/app/services/lexical_export.py` | Editor HTML → DOCX bytes. Pure, no DB |
| `backend/app/api/routes/submissions.py` | Serves `lexical_state`/`import_html`; persists both working-document views on revision |
| `frontend/components/editor/LexicalDocument.tsx` | The editor component; emits `{state, html}` together |
| `frontend/components/review/ReviewTab.tsx` | Chooses the editor or the legacy text pane |

---

### Task 1: Store a Lexical state alongside the original

**Files:**
- Create: `backend/alembic/versions/0033_lexical_state.py`
- Modify: `backend/app/models/submission.py`, `backend/app/models/submission_revision.py`
- Test: `backend/tests/test_lexical_state_column.py`

**Interfaces:**
- Produces: `Submission.lexical_state` / `SubmissionRevision.lexical_state` (`JSONB`, nullable) and `Submission.lexical_html` / `SubmissionRevision.lexical_html` (`Text`, nullable).

**Why two columns.** A Lexical editor state is a node tree; it contains no HTML. Rehydrating the editor needs the tree, but server-side export needs markup, and re-implementing Lexical's serializer in Python would be a second source of truth for the document's shape. So the client stores both: the tree it loads from, and the HTML it produced via `$generateHtmlFromNodes` at the same instant. They are written in the same transaction and must never be updated independently.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lexical_state_column.py
"""lexical_state must be nullable: every pre-existing submission has none,
and every code path has to keep working without it."""
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision


def test_submission_has_nullable_lexical_columns():
    for name in ("lexical_state", "lexical_html"):
        assert Submission.__table__.columns[name].nullable is True


def test_revision_has_nullable_lexical_columns():
    for name in ("lexical_state", "lexical_html"):
        assert SubmissionRevision.__table__.columns[name].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_lexical_state_column.py -q`
Expected: FAIL with `KeyError: 'lexical_state'`

- [ ] **Step 3: Add the columns**

```python
# backend/app/models/submission.py — add near current_content
from sqlalchemy.dialects.postgresql import JSONB
# ...
    # Working document. The uploaded file stays immutable; this is what the
    # reviewer edits. NULL for every submission created before this column.
    lexical_state = Column(JSONB, nullable=True)
    # The same document as HTML, serialized by the client at the same instant.
    # Export reads this; the editor reads lexical_state. Written together.
    lexical_html = Column(Text, nullable=True)
```

```python
# backend/app/models/submission_revision.py — add near content
from sqlalchemy.dialects.postgresql import JSONB
# ...
    lexical_state = Column(JSONB, nullable=True)
    lexical_html = Column(Text, nullable=True)
```

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/0033_lexical_state.py
"""lexical working document

Revision ID: 0033_lexical_state
Revises: 0032_widen_violation_category
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# NOTE: alembic revision IDs in this repo are bare 4-digit strings, NOT
# filenames. 0032_widen_violation_category.py declares revision = "0032".
# Using the filename here produces "Can't locate revision identified by ..."
# and crash-loops the backend container on startup.
revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("submissions", sa.Column("lexical_state", postgresql.JSONB(), nullable=True))
    op.add_column("submissions", sa.Column("lexical_html", sa.Text(), nullable=True))
    op.add_column("submission_revisions", sa.Column("lexical_state", postgresql.JSONB(), nullable=True))
    op.add_column("submission_revisions", sa.Column("lexical_html", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("submission_revisions", "lexical_html")
    op.drop_column("submission_revisions", "lexical_state")
    op.drop_column("submissions", "lexical_html")
    op.drop_column("submissions", "lexical_state")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_lexical_state_column.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Verify the whole suite still passes**

Run: `cd backend && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0033_lexical_state.py backend/app/models/submission.py backend/app/models/submission_revision.py backend/tests/test_lexical_state_column.py
git commit -m "feat(document): store a Lexical working state beside the immutable original"
```

---

### Task 2: Convert an uploaded DOCX to Lexical-compatible HTML

**Files:**
- Create: `backend/app/services/lexical_import.py`
- Modify: `backend/requirements.txt` (add `mammoth==1.8.0`)
- Test: `backend/tests/test_lexical_import.py`

**Interfaces:**
- Produces: `docx_to_html(docx_bytes: bytes) -> str` — returns a body-only HTML fragment. Raises `LexicalImportError` on unreadable input.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lexical_import.py
"""DOCX -> HTML is the import seam. Structure must survive: headings stay
headings, lists stay lists, tables stay tables. Anything that degrades to a
flat paragraph here is lost to the editor permanently."""
import io

import pytest
from docx import Document

from app.services.lexical_import import LexicalImportError, docx_to_html


def _docx(build) -> bytes:
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_heading_becomes_a_heading_element():
    data = _docx(lambda d: d.add_heading("Charges", level=2))
    html = docx_to_html(data)
    assert "<h2>" in html and "Charges" in html


def test_paragraph_becomes_a_paragraph():
    data = _docx(lambda d: d.add_paragraph("Returns are not guaranteed."))
    assert "<p>" in docx_to_html(data)


def test_bullet_list_becomes_a_list():
    def build(d):
        d.add_paragraph("first", style="List Bullet")
        d.add_paragraph("second", style="List Bullet")

    html = docx_to_html(_docx(build))
    assert "<ul>" in html and html.count("<li>") == 2


def test_table_becomes_a_table():
    def build(d):
        t = d.add_table(rows=1, cols=2)
        t.rows[0].cells[0].text = "a"
        t.rows[0].cells[1].text = "b"

    html = docx_to_html(_docx(build))
    assert "<table>" in html and "<td>" in html


def test_unreadable_input_raises_rather_than_returning_empty():
    with pytest.raises(LexicalImportError):
        docx_to_html(b"this is not a docx")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_lexical_import.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lexical_import'`

- [ ] **Step 3: Add the dependency**

Append to `backend/requirements.txt`:

```
mammoth==1.8.0
```

Then: `cd backend && pip install mammoth==1.8.0`

- [ ] **Step 4: Write the implementation**

```python
# backend/app/services/lexical_import.py
"""DOCX -> HTML, the import half of the Lexical working document.

The uploaded file is immutable, so this runs once at upload and its output is
converted to a Lexical state that the reviewer edits from then on. Fidelity
lost here is lost permanently, which is why the tests assert on structure
(headings, lists, tables) rather than on text.

mammoth is used rather than a hand-rolled OOXML walk: it already maps Word
styles onto semantic HTML, and the reverse direction (lexical_export) consumes
the same vocabulary.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class LexicalImportError(RuntimeError):
    """The upload could not be converted. Callers fall back to extracted text."""


# Word style -> HTML element. Explicit so an unmapped style is visible in
# review rather than silently flattening to <p>.
_STYLE_MAP = """
p[style-name='Heading 1'] => h1:fresh
p[style-name='Heading 2'] => h2:fresh
p[style-name='Heading 3'] => h3:fresh
p[style-name='Heading 4'] => h4:fresh
p[style-name='Title'] => h1:fresh
p[style-name='Quote'] => blockquote:fresh
b => strong
i => em
u => u
"""


def docx_to_html(docx_bytes: bytes) -> str:
    """Body-only HTML fragment for `docx_bytes`.

    Raises LexicalImportError rather than returning an empty string: an empty
    document and a failed conversion must not look identical to the caller.
    """
    if not docx_bytes:
        raise LexicalImportError("empty upload")
    try:
        import mammoth

        result = mammoth.convert_to_html(
            io.BytesIO(docx_bytes), style_map=_STYLE_MAP
        )
    except Exception as exc:  # noqa: BLE001 — any parse failure is one outcome
        raise LexicalImportError(str(exc)) from exc

    for message in result.messages:
        logger.info("lexical_import: %s", message)
    return result.value
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_lexical_import.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/lexical_import.py backend/tests/test_lexical_import.py backend/requirements.txt
git commit -m "feat(document): convert an uploaded DOCX to structured HTML for import"
```

---

### Task 3: Populate `lexical_state` on upload

**Files:**
- Modify: `backend/app/api/routes/submissions.py` (the `create_submission` background task area, and `get_submission`'s response)
- Create: `backend/app/services/lexical_document_service.py`
- Test: `backend/tests/test_lexical_document_service.py`

**Interfaces:**
- Consumes: `docx_to_html` from Task 2.
- Produces: `build_import_html(submission: Submission) -> Optional[str]` (no DB session — it reads the file only), and `GET /submissions/{id}` gaining `lexical_state` and `import_html` in its response.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lexical_document_service.py
"""Import must never mutate the upload, and must degrade rather than fail."""
import uuid

from app.models.submission import Submission
from app.services import lexical_document_service as svc


def test_non_docx_returns_none_rather_than_raising(tmp_path):
    sub = Submission(id=uuid.uuid4(), title="t", content_type="text",
                     original_content="hello", file_path=None)
    assert svc.build_import_html(sub) is None


def test_missing_file_returns_none(tmp_path):
    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(tmp_path / "absent.docx"))
    assert svc.build_import_html(sub) is None


def test_docx_returns_html_and_leaves_the_file_untouched(tmp_path):
    import io
    from docx import Document

    path = tmp_path / "a.docx"
    doc = Document()
    doc.add_heading("Charges", level=2)
    buf = io.BytesIO()
    doc.save(buf)
    path.write_bytes(buf.getvalue())
    before = path.read_bytes()

    sub = Submission(id=uuid.uuid4(), title="t", content_type="docx",
                     file_path=str(path))
    html = svc.build_import_html(sub)

    assert "<h2>" in html
    assert path.read_bytes() == before, "the upload is immutable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_lexical_document_service.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lexical_document_service'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/lexical_document_service.py
"""Bridges an uploaded submission to its Lexical working document.

Import is best-effort by design: a submission that cannot be converted keeps
working on extracted text exactly as before, because every pre-existing
submission is in that state and must stay usable.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.models.submission import Submission
from app.services.lexical_import import LexicalImportError, docx_to_html

logger = logging.getLogger(__name__)


def build_import_html(submission: Submission) -> Optional[str]:
    """HTML to seed this submission's editor, or None if it has no importable
    source. Reads the upload; never writes it."""
    if submission.content_type != "docx" or not submission.file_path:
        return None
    if not os.path.exists(submission.file_path):
        logger.warning("lexical import: upload missing for %s", submission.id)
        return None
    try:
        with open(submission.file_path, "rb") as f:
            return docx_to_html(f.read())
    except LexicalImportError as exc:
        logger.warning("lexical import failed for %s: %s", submission.id, exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_lexical_document_service.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Expose it on the submission response**

In `backend/app/api/routes/submissions.py`, inside `get_submission`'s returned dict, after `"current_content": submission.current_content,` add:

```python
        # Working document. `lexical_state` is authoritative once present;
        # `import_html` seeds the editor the first time, and is None for any
        # submission with no importable upload (which keeps the text pane).
        "lexical_state": submission.lexical_state,
        "import_html": (
            None if submission.lexical_state
            else lexical_document_service.build_import_html(submission)
        ),
```

And add the import at the top of the file:

```python
from app.services import lexical_document_service
```

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/lexical_document_service.py backend/tests/test_lexical_document_service.py backend/app/api/routes/submissions.py
git commit -m "feat(document): seed the working document from the uploaded DOCX"
```

---

### Task 4: Persist an edited Lexical state

**Files:**
- Modify: `backend/app/api/routes/submissions.py` (`create_revision`), `backend/app/schemas/submission.py`
- Test: `backend/tests/test_lexical_revision.py`

**Interfaces:**
- Consumes: `SubmissionRevision.lexical_state` from Task 1.
- Produces: `POST /submissions/{id}/revisions` accepting an optional `lexical_state` object; on save it writes to both `submission_revisions.lexical_state` and `submissions.lexical_state`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lexical_revision.py
"""A saved revision carries the Lexical state, and the submission's working
copy moves with it. `content` (plain text) is still written so findings,
search and the existing exports keep working during Phase 1."""
from app.schemas.submission import SubmissionRevisionCreate


def test_revision_payload_accepts_state_and_html():
    payload = SubmissionRevisionCreate(
        content="Returns are not guaranteed.",
        source="manual_edit",
        lexical_state={"root": {"children": []}},
        lexical_html="<p>Returns are not guaranteed.</p>",
    )
    assert payload.lexical_state["root"] == {"children": []}
    assert payload.lexical_html.startswith("<p>")


def test_both_lexical_fields_are_optional():
    payload = SubmissionRevisionCreate(content="x", source="manual_edit")
    assert payload.lexical_state is None
    assert payload.lexical_html is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_lexical_revision.py -q`
Expected: FAIL — `SubmissionRevisionCreate` has no `lexical_state`

- [ ] **Step 3: Add the field to the schema**

In `backend/app/schemas/submission.py`, on `SubmissionRevisionCreate`:

```python
    # Lexical editor state for this revision, and the HTML the client
    # serialized from it at the same instant. Optional: a text-only edit path
    # (and every pre-existing client) still posts just `content`.
    lexical_state: Optional[dict] = None
    lexical_html: Optional[str] = None
```

- [ ] **Step 4: Persist it in the route**

In `create_revision` in `backend/app/api/routes/submissions.py`, where the `SubmissionRevision(...)` is constructed, add `lexical_state=payload.lexical_state,` and `lexical_html=payload.lexical_html,`. Immediately after the existing `submission.current_content = payload.content` assignment, add:

```python
    # The working document moves with the revision. Only overwrite when the
    # client actually sent one, so a text-only save cannot blank it. State and
    # HTML are two views of one document — write both or neither, or export
    # would render a version the editor never shows.
    if payload.lexical_state is not None:
        submission.lexical_state = payload.lexical_state
        submission.lexical_html = payload.lexical_html
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_lexical_revision.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/submission.py backend/app/api/routes/submissions.py backend/tests/test_lexical_revision.py
git commit -m "feat(document): persist the Lexical state with each revision"
```

---

### Task 5: The editor component

**Files:**
- Create: `frontend/components/editor/LexicalDocument.tsx`
- Modify: `frontend/package.json`, `frontend/lib/types.ts`, `frontend/components/review/DocumentPane.tsx`
- Test: manual (the repo has no frontend test runner; adding one is out of scope)

**Interfaces:**
- Consumes: `lexical_state` / `import_html` from Task 3.
- Produces: `<LexicalDocument initialState={...} initialHtml={...} readOnly={...} onChange={(doc: {state: SerializedEditorState; html: string}) => void} />`. `onChange` emits both views of the document together, because the server stores both and they must never diverge.

- [ ] **Step 1: Add the dependencies**

```bash
cd frontend && npm install lexical@^0.48.0 @lexical/react@^0.48.0 @lexical/rich-text@^0.48.0 @lexical/list@^0.48.0 @lexical/table@^0.48.0 @lexical/link@^0.48.0 @lexical/html@^0.48.0 @lexical/utils@^0.48.0
```

- [ ] **Step 2: Add the types**

In `frontend/lib/types.ts`, on the `Submission` interface:

```typescript
  /** Working document. Authoritative once present; the uploaded file stays
   * immutable and is kept only as the original for audit. */
  lexical_state?: Record<string, unknown> | null;
  /** Seeds the editor on first open when lexical_state is still null. */
  import_html?: string | null;
```

- [ ] **Step 3: Write the component**

```tsx
// frontend/components/editor/LexicalDocument.tsx
"use client";
import * as React from "react";
import { $getRoot, $insertNodes, type SerializedEditorState } from "lexical";
import { $generateNodesFromDOM, $generateHtmlFromNodes } from "@lexical/html";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import { ListItemNode, ListNode } from "@lexical/list";
import { TableCellNode, TableNode, TableRowNode } from "@lexical/table";
import { AutoLinkNode, LinkNode } from "@lexical/link";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { RichTextPlugin } from "@lexical/react/LexicalRichTextPlugin";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { ListPlugin } from "@lexical/react/LexicalListPlugin";
import { LinkPlugin } from "@lexical/react/LexicalLinkPlugin";
import { TablePlugin } from "@lexical/react/LexicalTablePlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";

const NODES = [
  HeadingNode, QuoteNode, ListNode, ListItemNode,
  TableNode, TableRowNode, TableCellNode, AutoLinkNode, LinkNode,
];

/** Seeds the editor from imported HTML exactly once. Runs only when there is
 * no saved state — after the first save, lexical_state is authoritative and
 * re-seeding would discard the reviewer's edits. */
function SeedFromHtml({ html }: { html: string }) {
  const [editor] = useLexicalComposerContext();
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    editor.update(() => {
      const dom = new DOMParser().parseFromString(html, "text/html");
      const nodes = $generateNodesFromDOM(editor, dom);
      $getRoot().clear().select();
      $insertNodes(nodes);
    });
  }, [editor, html]);
  return null;
}

export function LexicalDocument({
  initialState,
  initialHtml,
  readOnly = false,
  onChange,
}: {
  initialState?: Record<string, unknown> | null;
  initialHtml?: string | null;
  readOnly?: boolean;
  onChange?: (doc: { state: SerializedEditorState; html: string }) => void;
}) {
  const config = {
    namespace: "compliance-document",
    editable: !readOnly,
    nodes: NODES,
    editorState: initialState ? JSON.stringify(initialState) : undefined,
    onError(error: Error) {
      // Never swallow: a thrown node error silently empties the document.
      throw error;
    },
  };

  return (
    <LexicalComposer initialConfig={config}>
      <div className="relative min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <RichTextPlugin
          contentEditable={<ContentEditable className="outline-none" />}
          placeholder={null}
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <ListPlugin />
        <LinkPlugin />
        <TablePlugin />
        {onChange && (
          <OnChangePlugin
            ignoreSelectionChange
            onChange={(editorState, editor) => {
              // Serialize both views in one pass, from the same state. Doing
              // them separately would let export render a document the editor
              // never displayed.
              editorState.read(() => {
                onChange({
                  state: editorState.toJSON(),
                  html: $generateHtmlFromNodes(editor, null),
                });
              });
            }}
          />
        )}
        {!initialState && initialHtml && <SeedFromHtml html={initialHtml} />}
      </div>
    </LexicalComposer>
  );
}
```

- [ ] **Step 4: Verify it typechecks and builds**

Run: `cd frontend && npx tsc --noEmit && npx next build --no-lint`
Expected: exit 0 from both

- [ ] **Step 5: Commit**

```bash
git add frontend/components/editor/LexicalDocument.tsx frontend/lib/types.ts frontend/package.json frontend/package-lock.json
git commit -m "feat(editor): rich-text document editor backed by Lexical"
```

---

### Task 6: Export the working document

**Files:**
- Create: `backend/app/services/lexical_export.py`
- Modify: `backend/app/services/submission_export_service.py`
- Delete: `backend/tests/test_clean_docx_preserves_formatting.py`
- Test: `backend/tests/test_lexical_export.py`

**Interfaces:**
- Consumes: `submissions.lexical_state` from Task 4.
- Produces: `lexical_html_to_docx(html: str, title: str) -> bytes`, used by `_clean_docx` when a `lexical_state` exists.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lexical_export.py
"""Export is generated from the working document, not from the upload.

The uploaded file is the immutable original; the approved artifact is this.
Structure must survive the round trip or the editor's fidelity is pointless.
"""
import io

from docx import Document

from app.services.lexical_export import lexical_html_to_docx


def _paragraphs(data: bytes):
    return [p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()]


def _styles(data: bytes):
    return [p.style.name for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()]


def test_headings_export_as_word_headings():
    data = lexical_html_to_docx("<h2>Charges</h2><p>Body copy.</p>", "t")
    assert "Charges" in _paragraphs(data)
    assert any(s.startswith("Heading") for s in _styles(data))


def test_list_items_export_as_list_paragraphs():
    data = lexical_html_to_docx("<ul><li>first</li><li>second</li></ul>", "t")
    assert "first" in _paragraphs(data) and "second" in _paragraphs(data)


def test_table_exports_as_a_table():
    data = lexical_html_to_docx("<table><tr><td>a</td><td>b</td></tr></table>", "t")
    doc = Document(io.BytesIO(data))
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[0].cells[0].text == "a"


def test_empty_html_produces_a_valid_document():
    """A blank document must not raise — it exports as an empty file."""
    assert lexical_html_to_docx("", "t")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_lexical_export.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lexical_export'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/lexical_export.py
"""HTML -> DOCX, the export half of the Lexical working document.

Runs server-side rather than in the browser so an export is reproducible and
auditable: the approved artifact must not depend on which machine produced it.
Consumes the same HTML vocabulary lexical_import emits.
"""
from __future__ import annotations

import io
from typing import List

from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.document import Document as DocxDocument

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _add_block(doc: DocxDocument, el: Tag) -> None:
    name = el.name
    if name in _HEADING_TAGS:
        doc.add_heading(el.get_text(strip=True), level=_HEADING_TAGS[name])
    elif name in ("ul", "ol"):
        style = "List Bullet" if name == "ul" else "List Number"
        for li in el.find_all("li", recursive=False):
            doc.add_paragraph(li.get_text(strip=True), style=style)
    elif name == "table":
        rows: List[Tag] = el.find_all("tr")
        if not rows:
            return
        cols = max(len(r.find_all(["td", "th"])) for r in rows)
        table = doc.add_table(rows=0, cols=cols)
        for r in rows:
            cells = r.find_all(["td", "th"])
            row = table.add_row()
            for i, cell in enumerate(cells[:cols]):
                row.cells[i].text = cell.get_text(strip=True)
    elif name == "blockquote":
        doc.add_paragraph(el.get_text(strip=True), style="Quote")
    else:
        text = el.get_text(strip=True)
        if text:
            doc.add_paragraph(text)


def lexical_html_to_docx(html: str, title: str) -> bytes:
    """DOCX bytes for the editor's HTML. An empty document is valid output."""
    doc = Document()
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.find_all(recursive=False):
        if isinstance(el, Tag):
            _add_block(doc, el)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_lexical_export.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Retire the write-back-into-the-original path**

In `backend/app/services/submission_export_service.py`, delete `_edited_original_docx` and `_replace_paragraph_text` entirely, and replace `_clean_docx` with:

```python
def _clean_docx(submission: Submission) -> bytes:
    """The corrected document.

    Generated from the working Lexical document when one exists — the uploaded
    file is the immutable original and is never edited. Submissions predating
    the editor have no working document and fall back to the plain-text rebuild.
    """
    if submission.lexical_html:
        return lexical_html_to_docx(submission.lexical_html, submission.title or "Submission")
    return _rebuilt_clean_docx(submission)
```

Add the import: `from app.services.lexical_export import lexical_html_to_docx`

Then delete the obsolete test file:

```bash
git rm backend/tests/test_clean_docx_preserves_formatting.py
```

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS. The deleted tests are gone; nothing else regresses.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/lexical_export.py backend/tests/test_lexical_export.py backend/app/services/submission_export_service.py
git commit -m "feat(export): generate the approved artifact from the working document"
```

---

### Task 7: Wire the editor into the review workspace

**Files:**
- Modify: `frontend/components/review/ReviewTab.tsx`, `frontend/components/workspace/SubmissionWorkspaceContext.tsx`, `frontend/lib/api.ts`

**Interfaces:**
- Consumes: `LexicalDocument` from Task 5, the revision endpoint from Task 4.

- [ ] **Step 1: Send the Lexical state when saving**

In `frontend/lib/api.ts`, extend the revision body type on `applySubmissionRevision`:

```typescript
  lexical_state?: Record<string, unknown>;
  lexical_html?: string;
```

- [ ] **Step 2: Carry it through the workspace context**

In `SubmissionWorkspaceContext.tsx`, add to the context type:

```typescript
  /** Latest editor state, sent with the next save. Null until the reviewer
   * edits in the rich editor. */
  lexicalDoc: { state: Record<string, unknown>; html: string } | null;
  setLexicalDoc: (d: { state: Record<string, unknown>; html: string }) => void;
```

Add `const [lexicalDoc, setLexicalDoc] = React.useState<{ state: Record<string, unknown>; html: string } | null>(null);`, include both in the `value` memo and its dependency array, and in `persist` pass `lexical_state: lexicalDoc?.state, lexical_html: lexicalDoc?.html` to `applySubmissionRevision`.

- [ ] **Step 3: Render the editor in Edit mode**

In `ReviewTab.tsx`, replace the `DocumentPane` branch of the View/Edit toggle:

```tsx
        ) : submission.lexical_state || submission.import_html ? (
          <LexicalDocument
            initialState={submission.lexical_state}
            initialHtml={submission.import_html}
            readOnly={isHistorical}
            onChange={setLexicalDoc}
          />
        ) : (
          <DocumentPane
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={setSelectedViolationId}
            readOnly={isHistorical}
          />
        )}
```

Import it: `import { LexicalDocument } from "@/components/editor/LexicalDocument";`

The `DocumentPane` fallback stays. Findings are still anchored to extracted text in Phase 1, so a submission with no working document keeps the highlighting it has today.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit && npx next build --no-lint`
Expected: exit 0 from both

- [ ] **Step 5: Manual check**

Upload a DOCX with a heading, a bullet list and a table. Confirm: View mode still shows the faithful page render; Edit mode shows the heading as a heading, the list as a list, the table as a table; edit a sentence, save, reload, and the edit persists; export `clean.docx` and confirm the structure survives.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/review/ReviewTab.tsx frontend/components/workspace/SubmissionWorkspaceContext.tsx frontend/lib/api.ts
git commit -m "feat(review): edit the working document in the rich editor"
```

---

## Known limitation at the end of Phase 1

Findings still anchor to extracted-text character offsets. In the Lexical editor those offsets drift as soon as the reviewer edits, so violation highlighting is **not** reliable inside the rich editor — that is exactly what Phase 2's node-key-plus-fingerprint anchoring fixes. Until Phase 2 lands, keep `DocumentPane` as the highlighting surface and treat the Lexical editor as the editing surface. Do not delete `highlightMarkup.ts`.
