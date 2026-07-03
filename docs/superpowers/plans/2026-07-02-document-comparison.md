# Document Comparison ("Compare") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user upload two versions of a document (or paste text) and see a word-level, side-by-side redline of what changed — a standalone feature, decoupled from compliance scoring.

**Architecture:** New backend table + service (`difflib`-based two-pass diff: paragraph alignment, then word diff within replaced pairs) + 4 REST endpoints, plus new frontend routes under `/compare` that upload, list, and render the persisted diff. No LLM calls anywhere in this feature — everything is synchronous, pure text processing.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend, existing stack), Next.js App Router + Tailwind (frontend, existing stack). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-02-document-comparison-design.md`

## Global Constraints

- No LLM calls anywhere in this feature (per spec's non-goals) — diff computation is pure `difflib`, so `POST /comparisons` is synchronous.
- No new Python or npm dependencies — extraction reuses `python-docx`/`pdfplumber` (already in `backend/requirements.txt`), diffing uses stdlib `difflib`.
- File upload size/type limits reuse `settings.max_upload_size` (50MB) and `settings.upload_dir` (`backend/app/config.py:194-195`).
- No move/relocation detection (delete-in-place + insert-in-place is correct behavior for a moved block, per spec).
- No formatting/visual fidelity — diff view renders extracted plain text only.
- This project has no frontend test runner configured (no jest/vitest in `frontend/package.json`) — frontend tasks verify via `npx tsc --noEmit` (typecheck) plus a final manual browser pass; do not introduce a new test framework as part of this feature.
- Backend tests follow this repo's existing convention: pure-function unit tests for service logic (no DB), lightweight smoke tests for route registration (no `TestClient`/DB fixture — none exists in this repo; see `backend/tests/test_kb_router_smoke.py`).
- Branch: `feature/document-comparison` (already created). Commit after every task.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/models/document_comparison.py` (new) | `DocumentComparison` ORM model |
| `backend/alembic/versions/0013_document_comparisons.py` (new) | Creates `document_comparisons` table |
| `backend/app/services/comparison_service.py` (new) | Pure functions: paragraph extraction (docx/pdf/text) + paragraph/word diff. No I/O beyond reading the given file path. |
| `backend/app/api/routes/comparisons.py` (new) | POST/GET/GET/DELETE endpoints; file save mirrors `submissions.py` |
| `backend/app/models/__init__.py` (modify) | Export `DocumentComparison` |
| `backend/app/main.py` (modify) | Register `comparisons` router + model import |
| `frontend/lib/types.ts` (modify) | `DocumentComparison`, `DiffBlock`, `DiffWord`, `ComparisonStatus` types |
| `frontend/lib/api.ts` (modify) | `listComparisons`, `getComparison`, `createComparison`, `deleteComparison` |
| `frontend/components/compare/DiffViewer.tsx` (new) | Renders `DiffBlock[]` as a two-column, single-scroll redline table |
| `frontend/app/(workspace)/compare/page.tsx` (new) | History list |
| `frontend/app/(workspace)/compare/new/page.tsx` (new) | Upload/paste form for both sides |
| `frontend/app/(workspace)/compare/[id]/page.tsx` (new) | Diff viewer detail page |
| `frontend/components/workspace/Sidebar.tsx` (modify) | Add "Compare" nav item |
| `frontend/components/workspace/CommandPalette.tsx` (modify) | Add "Compare" to ⌘K nav entries |

---

## Task 1: `DocumentComparison` model

**Files:**
- Create: `backend/app/models/document_comparison.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_document_comparison_model.py`

**Interfaces:**
- Produces: `DocumentComparison` ORM class with columns `id, title, old_content_type, new_content_type, old_file_path, new_file_path, old_original_content, new_original_content, diff_result, status, error_message, created_by, created_at`. `__tablename__ = "document_comparisons"`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_document_comparison_model.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_table_name_and_columns():
    from app.models.document_comparison import DocumentComparison
    assert DocumentComparison.__tablename__ == "document_comparisons"
    columns = {c.name for c in DocumentComparison.__table__.columns}
    assert columns == {
        "id", "title", "old_content_type", "new_content_type",
        "old_file_path", "new_file_path", "old_original_content",
        "new_original_content", "diff_result", "status", "error_message",
        "created_by", "created_at",
    }


def test_registered_on_base_metadata():
    from app.models.document_comparison import DocumentComparison  # noqa: F401
    from app.database import Base
    assert "document_comparisons" in Base.metadata.tables
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `pytest tests/test_document_comparison_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.document_comparison'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/models/document_comparison.py
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from ..database import Base


class DocumentComparison(Base):
    __tablename__ = "document_comparisons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    old_content_type = Column(String(50), nullable=False)  # docx, pdf, text
    new_content_type = Column(String(50), nullable=False)
    old_file_path = Column(String(1000), nullable=True)
    new_file_path = Column(String(1000), nullable=True)
    old_original_content = Column(Text, nullable=True)
    new_original_content = Column(Text, nullable=True)
    diff_result = Column(JSONB, nullable=True)
    status = Column(String(50), default="processing")  # processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

Also register it in `backend/app/models/__init__.py`:

```python
# Add this import near the other model imports:
from .document_comparison import DocumentComparison

# Add "DocumentComparison" to __all__:
__all__ = [
    "User",
    "Submission",
    "Rule",
    "ComplianceCheck",
    "Violation",
    "ContentChunk",
    "AgentExecution",
    "AgentTrace",
    "ToolInvocation",
    "ComplianceState",
    "RuleFeedback",
    "ProductDocument",
    "ProductTable",
    "DocumentComparison",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_document_comparison_model.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/document_comparison.py backend/app/models/__init__.py backend/tests/test_document_comparison_model.py
git commit -m "feat: add DocumentComparison model"
```

---

## Task 2: Alembic migration

**Files:**
- Create: `backend/alembic/versions/0013_document_comparisons.py`

**Interfaces:**
- Consumes: column shape from Task 1's `DocumentComparison` model.
- Produces: `document_comparisons` table in the database, revision `0013` (down_revision `0012`).

- [ ] **Step 1: Write the migration**

```python
# backend/alembic/versions/0013_document_comparisons.py
"""document_comparisons — persisted diff results for the standalone Compare tool.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("old_content_type", sa.String(length=50), nullable=False),
        sa.Column("new_content_type", sa.String(length=50), nullable=False),
        sa.Column("old_file_path", sa.String(length=1000), nullable=True),
        sa.Column("new_file_path", sa.String(length=1000), nullable=True),
        sa.Column("old_original_content", sa.Text(), nullable=True),
        sa.Column("new_original_content", sa.Text(), nullable=True),
        sa.Column("diff_result", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="processing"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_document_comparisons_status", "document_comparisons", ["status"])


def downgrade() -> None:
    op.drop_index("ix_document_comparisons_status", table_name="document_comparisons")
    op.drop_table("document_comparisons")
```

- [ ] **Step 2: Verify the migration chain is valid (static check, no DB required)**

Run (from `backend/`): `python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; sd = ScriptDirectory.from_config(Config('alembic.ini')); print(sd.get_revision('0013'))"`
Expected: prints the `0013` `Script` object with no traceback (confirms the revision links cleanly to `0012` without needing a live database — per project convention, do NOT run `alembic upgrade head` against a live DB as part of this task; that is an explicit user-run step).

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/0013_document_comparisons.py
git commit -m "feat: add document_comparisons migration"
```

---

## Task 3: Paragraph extraction (`comparison_service.py`, part 1)

**Files:**
- Create: `backend/app/services/comparison_service.py`
- Test: `backend/tests/services/test_comparison_service.py`

**Interfaces:**
- Produces:
  - `split_text_paragraphs(text: str) -> list[str]`
  - `extract_docx_paragraphs(file_path: str) -> list[str]`
  - `extract_pdf_paragraphs(file_path: str) -> list[str]`
  - `extract_paragraphs(file_path: str | None, content_type: str, pasted_text: str | None = None) -> list[str]` — dispatches on `content_type` (`"docx"`, `"pdf"`, else treated as pasted text).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_comparison_service.py
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.comparison_service import (
    split_text_paragraphs,
    extract_docx_paragraphs,
    extract_pdf_paragraphs,
    extract_paragraphs,
)


def test_split_text_paragraphs_splits_on_blank_lines():
    text = "First paragraph.\n\nSecond paragraph.\n\n\nThird paragraph."
    assert split_text_paragraphs(text) == [
        "First paragraph.",
        "Second paragraph.",
        "Third paragraph.",
    ]


def test_split_text_paragraphs_empty_string_returns_empty_list():
    assert split_text_paragraphs("") == []
    assert split_text_paragraphs("   ") == []


def test_extract_docx_paragraphs_reads_paragraphs_and_tags_headings(tmp_path: Path):
    from docx import Document
    doc = Document()
    doc.add_heading("Policy Overview", level=1)
    doc.add_paragraph("This plan offers guaranteed returns.")
    doc.add_paragraph("")  # blank paragraph should be skipped
    doc.add_paragraph("Terms and conditions apply.")
    file_path = tmp_path / "sample.docx"
    doc.save(str(file_path))

    paragraphs = extract_docx_paragraphs(str(file_path))

    assert paragraphs == [
        "## Policy Overview",
        "This plan offers guaranteed returns.",
        "Terms and conditions apply.",
    ]


def test_extract_pdf_paragraphs_splits_page_text_on_blank_lines():
    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Para one.\n\nPara two."
    fake_pdf = MagicMock()
    fake_pdf.__enter__.return_value.pages = [fake_page]
    fake_pdf.__exit__.return_value = False

    with patch("pdfplumber.open", return_value=fake_pdf):
        paragraphs = extract_pdf_paragraphs("fake.pdf")

    assert paragraphs == ["Para one.", "Para two."]


def test_extract_paragraphs_dispatches_by_content_type(tmp_path: Path):
    text_paragraphs = extract_paragraphs(None, "text", "Hello world.\n\nSecond line.")
    assert text_paragraphs == ["Hello world.", "Second line."]

    from docx import Document
    doc = Document()
    doc.add_paragraph("Docx body text.")
    file_path = tmp_path / "sample.docx"
    doc.save(str(file_path))
    docx_paragraphs = extract_paragraphs(str(file_path), "docx")
    assert docx_paragraphs == ["Docx body text."]


def test_extract_paragraphs_requires_file_path_for_docx_and_pdf():
    with pytest.raises(ValueError):
        extract_paragraphs(None, "docx")
    with pytest.raises(ValueError):
        extract_paragraphs(None, "pdf")
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `pytest tests/services/test_comparison_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.comparison_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/comparison_service.py
"""
Document comparison service — paragraph extraction and word-level diffing.

No LLM calls; pure text processing (python-docx / pdfplumber for extraction,
stdlib difflib for diffing), so comparisons run synchronously in the API layer.
"""
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)


def split_text_paragraphs(text: str) -> List[str]:
    """Split raw text into paragraphs on one-or-more blank lines."""
    if not text or not text.strip():
        return []
    blocks = re.split(r"\n\s*\n", text.strip())
    return [b.strip() for b in blocks if b.strip()]


def extract_docx_paragraphs(file_path: str) -> List[str]:
    """Extract paragraph text from a DOCX file, one entry per Word paragraph."""
    from docx import Document
    doc = Document(file_path)
    paragraphs: List[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = getattr(para.style, "name", "") or ""
        if style.startswith("Heading") or style == "Title":
            paragraphs.append(f"## {text}")
        else:
            paragraphs.append(text)
    return paragraphs


def extract_pdf_paragraphs(file_path: str) -> List[str]:
    """Extract paragraph text from a PDF: page text joined, then split on blank lines."""
    import pdfplumber
    pages: List[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    return split_text_paragraphs("\n\n".join(pages))


def extract_paragraphs(
    file_path: Optional[str], content_type: str, pasted_text: Optional[str] = None
) -> List[str]:
    """Dispatch extraction by content_type: docx/pdf read from file_path, else split pasted_text."""
    if content_type == "docx":
        if not file_path:
            raise ValueError("docx content_type requires file_path")
        return extract_docx_paragraphs(file_path)
    if content_type == "pdf":
        if not file_path:
            raise ValueError("pdf content_type requires file_path")
        return extract_pdf_paragraphs(file_path)
    return split_text_paragraphs(pasted_text or "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_comparison_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/comparison_service.py backend/tests/services/test_comparison_service.py
git commit -m "feat: add paragraph extraction for document comparison"
```

---

## Task 4: Diff engine (`comparison_service.py`, part 2)

**Files:**
- Modify: `backend/app/services/comparison_service.py`
- Modify: `backend/tests/services/test_comparison_service.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces:
  - `word_diff(old_text: str, new_text: str) -> dict` — `{"type": "replace", "old_words": [{"text": str, "changed": bool}, ...], "new_words": [...]}`
  - `build_diff(old_paragraphs: list[str], new_paragraphs: list[str]) -> list[dict]` — ordered list of blocks, each one of:
    - `{"type": "equal", "old_text": str, "new_text": str}`
    - `{"type": "delete", "old_text": str}`
    - `{"type": "insert", "new_text": str}`
    - the `word_diff` shape above (for replaced pairs)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_comparison_service.py`:

```python
from app.services.comparison_service import word_diff, build_diff


def test_word_diff_marks_changed_and_unchanged_words():
    result = word_diff("The quick brown fox", "The slow brown fox")
    assert result["type"] == "replace"
    assert result["old_words"] == [
        {"text": "The", "changed": False},
        {"text": "quick", "changed": True},
        {"text": "brown", "changed": False},
        {"text": "fox", "changed": False},
    ]
    assert result["new_words"] == [
        {"text": "The", "changed": False},
        {"text": "slow", "changed": True},
        {"text": "brown", "changed": False},
        {"text": "fox", "changed": False},
    ]


def test_build_diff_all_equal_when_paragraphs_identical():
    old = ["First paragraph.", "Second paragraph."]
    new = ["First paragraph.", "Second paragraph."]
    assert build_diff(old, new) == [
        {"type": "equal", "old_text": "First paragraph.", "new_text": "First paragraph."},
        {"type": "equal", "old_text": "Second paragraph.", "new_text": "Second paragraph."},
    ]


def test_build_diff_pure_insert():
    assert build_diff([], ["New paragraph."]) == [
        {"type": "insert", "new_text": "New paragraph."}
    ]


def test_build_diff_pure_delete():
    assert build_diff(["Old paragraph."], []) == [
        {"type": "delete", "old_text": "Old paragraph."}
    ]


def test_build_diff_replace_same_count_runs_word_diff():
    old = ["The quick brown fox."]
    new = ["The slow brown fox."]
    blocks = build_diff(old, new)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "replace"
    assert blocks[0]["old_words"][1] == {"text": "quick", "changed": True}
    assert blocks[0]["new_words"][1] == {"text": "slow", "changed": True}


def test_build_diff_replace_different_count_falls_back_to_delete_insert():
    old = ["One paragraph that got split."]
    new = ["One paragraph.", "That got split."]
    assert build_diff(old, new) == [
        {"type": "delete", "old_text": "One paragraph that got split."},
        {"type": "insert", "new_text": "One paragraph."},
        {"type": "insert", "new_text": "That got split."},
    ]


def test_build_diff_empty_documents_produce_no_change():
    assert build_diff([], []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `backend/`): `pytest tests/services/test_comparison_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'word_diff'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/services/comparison_service.py` (add `from difflib import SequenceMatcher` to the existing imports at the top):

```python
# Add to imports at top of file:
from difflib import SequenceMatcher


def word_diff(old_text: str, new_text: str) -> dict:
    """Word-level diff between two paragraphs assumed to be aligned (same position)."""
    old_words = old_text.split()
    new_words = new_text.split()
    matcher = SequenceMatcher(None, old_words, new_words)
    old_out = []
    new_out = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changed = tag != "equal"
        for w in old_words[i1:i2]:
            old_out.append({"text": w, "changed": changed})
        for w in new_words[j1:j2]:
            new_out.append({"text": w, "changed": changed})
    return {"type": "replace", "old_words": old_out, "new_words": new_out}


def build_diff(old_paragraphs: List[str], new_paragraphs: List[str]) -> List[dict]:
    """Align two paragraph lists and word-diff replaced pairs. Returns ordered diff blocks."""
    matcher = SequenceMatcher(None, old_paragraphs, new_paragraphs)
    blocks: List[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                blocks.append({
                    "type": "equal",
                    "old_text": old_paragraphs[i1 + k],
                    "new_text": new_paragraphs[j1 + k],
                })
        elif tag == "delete":
            for p in old_paragraphs[i1:i2]:
                blocks.append({"type": "delete", "old_text": p})
        elif tag == "insert":
            for p in new_paragraphs[j1:j2]:
                blocks.append({"type": "insert", "new_text": p})
        elif tag == "replace":
            old_slice = old_paragraphs[i1:i2]
            new_slice = new_paragraphs[j1:j2]
            if len(old_slice) == len(new_slice):
                for op, np in zip(old_slice, new_slice):
                    blocks.append(word_diff(op, np))
            else:
                for p in old_slice:
                    blocks.append({"type": "delete", "old_text": p})
                for p in new_slice:
                    blocks.append({"type": "insert", "new_text": p})
    return blocks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_comparison_service.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/comparison_service.py backend/tests/services/test_comparison_service.py
git commit -m "feat: add paragraph alignment + word diff engine"
```

---

## Task 5: `/comparisons` API routes

**Files:**
- Create: `backend/app/api/routes/comparisons.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_comparisons_router_smoke.py`

**Interfaces:**
- Consumes: `DocumentComparison` (Task 1), `extract_paragraphs` + `build_diff` (Tasks 3-4).
- Produces: `router` (FastAPI `APIRouter`, prefix `/comparisons`) with `POST /comparisons`, `GET /comparisons`, `GET /comparisons/{comparison_id}`, `DELETE /comparisons/{comparison_id}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_comparisons_router_smoke.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import comparisons


def test_router_prefix_and_routes():
    paths = {r.path for r in comparisons.router.routes}
    assert "/comparisons" in paths
    assert "/comparisons/{comparison_id}" in paths


def test_registered_in_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/comparisons" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `pytest tests/test_comparisons_router_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.routes.comparisons'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/api/routes/comparisons.py
"""
Document Comparison API Routes

Upload two versions of a document (or paste text) and get a persisted,
word-level diff between them. No LLM calls — pure text processing, so
requests are handled synchronously.
"""
import os
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.document_comparison import DocumentComparison
from app.config import settings
from app.services.comparison_service import extract_paragraphs, build_diff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comparisons", tags=["Comparisons"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "text",
}


async def _persist_upload(file: UploadFile):
    """Save an uploaded file under settings.upload_dir; returns (file_path, content_type)."""
    mime_type = file.content_type or ""
    detected_type = ALLOWED_CONTENT_TYPES.get(mime_type, "text")
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "txt"
    file_path = os.path.join(settings.upload_dir, f"{file_id}.{ext}")

    file_size = 0
    with open(file_path, "wb") as f:
        while chunk := await file.read(8192):
            file_size += len(chunk)
            if file_size > settings.max_upload_size:
                f.close()
                os.remove(file_path)
                raise HTTPException(status_code=413, detail="File too large")
            f.write(chunk)
    return file_path, detected_type


def _serialize(c: DocumentComparison, include_diff: bool = False) -> dict:
    data = {
        "id": str(c.id),
        "title": c.title,
        "old_content_type": c.old_content_type,
        "new_content_type": c.new_content_type,
        "status": c.status,
        "error_message": c.error_message,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if include_diff:
        data["diff_result"] = c.diff_result
    return data


@router.post("")
async def create_comparison(
    title: str = Form(...),
    old_content: Optional[str] = Form(default=None),
    new_content: Optional[str] = Form(default=None),
    old_file: Optional[UploadFile] = File(default=None),
    new_file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Create a comparison: saves any uploaded files, computes the diff inline, and persists it."""
    has_old = bool(old_file and old_file.filename) or bool((old_content or "").strip())
    has_new = bool(new_file and new_file.filename) or bool((new_content or "").strip())
    if not has_old:
        raise HTTPException(status_code=400, detail="Provide old_file or old_content")
    if not has_new:
        raise HTTPException(status_code=400, detail="Provide new_file or new_content")

    old_file_path = None
    new_file_path = None
    old_content_type = "text"
    new_content_type = "text"

    if old_file and old_file.filename:
        old_file_path, old_content_type = await _persist_upload(old_file)
    if new_file and new_file.filename:
        new_file_path, new_content_type = await _persist_upload(new_file)

    comparison = DocumentComparison(
        title=title,
        old_content_type=old_content_type,
        new_content_type=new_content_type,
        old_file_path=old_file_path,
        new_file_path=new_file_path,
        old_original_content=old_content,
        new_original_content=new_content,
        status="processing",
    )

    try:
        old_paragraphs = extract_paragraphs(old_file_path, old_content_type, old_content)
        new_paragraphs = extract_paragraphs(new_file_path, new_content_type, new_content)
        comparison.diff_result = build_diff(old_paragraphs, new_paragraphs)
        comparison.status = "completed"
    except Exception as e:
        logger.error(f"Comparison failed for '{title}': {e}")
        comparison.status = "failed"
        comparison.error_message = str(e)

    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    return _serialize(comparison, include_diff=True)


@router.get("")
async def list_comparisons(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List past comparisons, most recent first."""
    query = db.query(DocumentComparison).order_by(DocumentComparison.created_at.desc())
    total = query.count()
    comparisons = query.offset(skip).limit(limit).all()
    return {
        "total": total,
        "comparisons": [_serialize(c) for c in comparisons],
    }


@router.get("/{comparison_id}")
async def get_comparison(comparison_id: str, db: Session = Depends(get_db)):
    """Get a comparison, including its full diff result."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return _serialize(comparison, include_diff=True)


@router.delete("/{comparison_id}")
async def delete_comparison(comparison_id: str, db: Session = Depends(get_db)):
    """Delete a comparison and any files it saved to disk."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")

    for path in (comparison.old_file_path, comparison.new_file_path):
        if path and os.path.exists(path):
            os.remove(path)

    db.delete(comparison)
    db.commit()
    return {"message": "Comparison deleted", "id": comparison_id}
```

Register the router in `backend/app/main.py`:

```python
# Change this line (main.py:6):
from .api.routes import submissions, compliance, dashboard, rules, chat, similar, rag_health, knowledge_base, comparisons

# Add this line after the other app.include_router(...) calls (main.py:107-114):
app.include_router(comparisons.router)
```

Also add `DocumentComparison` to the model-registration import inside `lifespan()` (`main.py:27-31`), for consistency with the existing "models imported here so SQLAlchemy registers them on Base.metadata" comment:

```python
from .models import (
    User, Submission, Rule, ComplianceCheck,
    Violation, ContentChunk, AgentExecution,
    AgentTrace, ToolInvocation, ComplianceState,
    DocumentComparison,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_comparisons_router_smoke.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run (from `backend/`): `pytest tests/ -v`
Expected: all tests pass (no regressions from the router/model registration changes)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/comparisons.py backend/app/main.py backend/tests/test_comparisons_router_smoke.py
git commit -m "feat: add /comparisons API routes"
```

---

## Task 6: Frontend types + API client

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`

**Interfaces:**
- Produces:
  - `types.ts`: `ComparisonStatus`, `DiffWord`, `DiffBlock`, `DocumentComparison`
  - `api.ts`: `listComparisons()`, `getComparison(id)`, `createComparison(body)`, `deleteComparison(id)`

- [ ] **Step 1: Add types**

Append to `frontend/lib/types.ts`:

```typescript
export type ComparisonStatus = "processing" | "completed" | "failed";

export interface DiffWord {
  text: string;
  changed: boolean;
}

export type DiffBlock =
  | { type: "equal"; old_text: string; new_text: string }
  | { type: "delete"; old_text: string }
  | { type: "insert"; new_text: string }
  | { type: "replace"; old_words: DiffWord[]; new_words: DiffWord[] };

export interface DocumentComparison {
  id: string;
  title: string;
  old_content_type: string;
  new_content_type: string;
  status: ComparisonStatus;
  error_message?: string | null;
  diff_result?: DiffBlock[] | null;
  created_at: string;
}
```

- [ ] **Step 2: Add API client functions**

Append to `frontend/lib/api.ts` (add `DocumentComparison` to the existing `import type { ... } from "./types"` block at the top, then add this new section at the end of the file):

```typescript
/* ---------- comparisons ---------- */
export async function listComparisons(): Promise<{ total: number; comparisons: DocumentComparison[] }> {
  return jsonFetch(`${base()}/comparisons`);
}
export async function getComparison(id: string): Promise<DocumentComparison> {
  return jsonFetch(`${base()}/comparisons/${id}`);
}
export async function createComparison(body: {
  title: string;
  old_file?: File;
  new_file?: File;
  old_content?: string;
  new_content?: string;
}): Promise<DocumentComparison> {
  const form = new FormData();
  form.append("title", body.title);
  if (body.old_file) form.append("old_file", body.old_file);
  if (body.new_file) form.append("new_file", body.new_file);
  if (body.old_content) form.append("old_content", body.old_content);
  if (body.new_content) form.append("new_content", body.new_content);
  const res = await fetch(`${base()}/comparisons`, {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as DocumentComparison;
}
export async function deleteComparison(id: string): Promise<{ message: string }> {
  return jsonFetch(`${base()}/comparisons/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/api.ts
git commit -m "feat: add comparison types and API client functions"
```

---

## Task 7: `DiffViewer` component

**Files:**
- Create: `frontend/components/compare/DiffViewer.tsx`

**Interfaces:**
- Consumes: `DiffBlock`, `DiffWord` types (Task 6).
- Produces: `DiffViewer({ blocks }: { blocks: DiffBlock[] })` — a single-scroll, two-column redline table. Rows are aligned by rendering both sides of every block in the same grid row (delete blocks render an empty right cell, insert blocks render an empty left cell), so the two columns stay visually in sync without any scroll-position JavaScript.

- [ ] **Step 1: Write the component**

```tsx
// frontend/components/compare/DiffViewer.tsx
"use client";
import type { DiffBlock } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DiffViewer({ blocks }: { blocks: DiffBlock[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
      <div className="grid grid-cols-2 border-b border-border bg-muted/30 text-xs">
        <div className="border-r border-border px-4 py-2 micro-label">Original</div>
        <div className="px-4 py-2 micro-label">Revised</div>
      </div>
      <div className="max-h-[70vh] overflow-y-auto">
        {blocks.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No content to compare.
          </div>
        ) : (
          blocks.map((block, idx) => <DiffRow key={idx} block={block} />)
        )}
      </div>
    </div>
  );
}

function DiffRow({ block }: { block: DiffBlock }) {
  return (
    <div className="grid grid-cols-2 border-b border-border text-[13px] leading-relaxed last:border-0">
      <div className="border-r border-border px-4 py-2">{renderOld(block)}</div>
      <div className="px-4 py-2">{renderNew(block)}</div>
    </div>
  );
}

function renderOld(block: DiffBlock) {
  if (block.type === "equal") return <span>{block.old_text}</span>;
  if (block.type === "insert") return null;
  if (block.type === "delete") {
    return (
      <span className="bg-sev-critical/10 text-sev-critical line-through">{block.old_text}</span>
    );
  }
  return (
    <span>
      {block.old_words.map((w, i) => (
        <span key={i} className={cn(w.changed && "bg-sev-critical/10 text-sev-critical line-through")}>
          {w.text}{" "}
        </span>
      ))}
    </span>
  );
}

function renderNew(block: DiffBlock) {
  if (block.type === "equal") return <span>{block.new_text}</span>;
  if (block.type === "delete") return null;
  if (block.type === "insert") {
    return <span className="bg-success/10 text-success">{block.new_text}</span>;
  }
  return (
    <span>
      {block.new_words.map((w, i) => (
        <span key={i} className={cn(w.changed && "bg-success/10 text-success")}>
          {w.text}{" "}
        </span>
      ))}
    </span>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/components/compare/DiffViewer.tsx
git commit -m "feat: add DiffViewer component"
```

---

## Task 8: Compare history list page

**Files:**
- Create: `frontend/app/(workspace)/compare/page.tsx`

**Interfaces:**
- Consumes: `listComparisons()` (Task 6), `DocumentComparison` type (Task 6).

- [ ] **Step 1: Write the page**

```tsx
// frontend/app/(workspace)/compare/page.tsx
import Link from "next/link";
import { ArrowUpRight, GitCompare } from "lucide-react";
import { listComparisons } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { formatDate } from "@/lib/format";
import type { DocumentComparison } from "@/lib/types";

export const dynamic = "force-dynamic";

function comparisonStatusTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "processing") return "info" as const;
  return "danger" as const;
}

export default async function ComparePage() {
  let items: DocumentComparison[] = [];
  let err: string | null = null;
  try {
    const data = await listComparisons();
    items = data.comparisons ?? [];
  } catch (e) {
    err = (e as Error).message;
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="Compare"
        description="Upload two versions of a document to see exactly what changed — word-level, side by side."
        actions={
          <Button asChild size="hero">
            <Link href="/compare/new">New comparison →</Link>
          </Button>
        }
        meta={<PageHeaderMeta label="Total" value={items.length} />}
      />

      {err ? (
        <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 text-sm text-sev-critical">
          {err}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border py-16 text-center">
          <GitCompare className="h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">No comparisons yet.</p>
          <Button asChild size="sm">
            <Link href="/compare/new">Create your first comparison →</Link>
          </Button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-left">
                <th className="px-3 py-2.5 micro-label">Title</th>
                <th className="px-3 py-2.5 micro-label w-[120px]">Status</th>
                <th className="px-3 py-2.5 micro-label w-[140px]">Created</th>
                <th className="px-3 py-2.5 w-[40px]"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-border last:border-0 hover:bg-muted/40 transition-colors"
                >
                  <td className="px-3 py-2.5">
                    <Link href={`/compare/${c.id}`} className="font-medium hover:text-primary">
                      {c.title}
                    </Link>
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusPill tone={comparisonStatusTone(c.status)}>{c.status}</StatusPill>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                    {formatDate(c.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Link
                      href={`/compare/${c.id}`}
                      className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/app/\(workspace\)/compare/page.tsx
git commit -m "feat: add comparison history list page"
```

---

## Task 9: New comparison upload page

**Files:**
- Create: `frontend/app/(workspace)/compare/new/page.tsx`

**Interfaces:**
- Consumes: `createComparison()` (Task 6).

- [ ] **Step 1: Write the page**

```tsx
// frontend/app/(workspace)/compare/new/page.tsx
"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { createComparison } from "@/lib/api";

const ACCEPTED_EXT = ".pdf,.docx,.txt";
const MAX_FILE_MB = 50;

function SideInput({
  label,
  text,
  setText,
  file,
  setFile,
}: {
  label: string;
  text: string;
  setText: (v: string) => void;
  file: File | null;
  setFile: (f: File | null) => void;
}) {
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  return (
    <div>
      <label className="micro-label mb-2 block">{label}</label>
      <Tabs defaultValue="paste">
        <TabsList>
          <TabsTrigger value="paste">Paste text</TabsTrigger>
          <TabsTrigger value="upload">Upload file</TabsTrigger>
        </TabsList>
        <TabsContent value="paste" className="pt-3">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={`Paste the ${label.toLowerCase()} text here…`}
            rows={12}
            className="min-h-[220px] resize-y text-[14px] leading-[1.7]"
          />
        </TabsContent>
        <TabsContent value="upload" className="pt-3">
          <div
            onClick={() => fileInputRef.current?.click()}
            className="flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-border bg-surface p-6 text-center hover:border-foreground transition-colors"
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_EXT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const sizeMb = f.size / (1024 * 1024);
                if (sizeMb > MAX_FILE_MB) {
                  toast.error(`File too large (${sizeMb.toFixed(1)} MB). Limit is ${MAX_FILE_MB} MB.`);
                  return;
                }
                setFile(f);
              }}
            />
            {file ? (
              <>
                <div className="text-sm">{file.name}</div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                  className="text-[11px] text-muted-foreground underline hover:text-foreground"
                >
                  Choose a different file
                </button>
              </>
            ) : (
              <div className="text-sm text-muted-foreground">
                Drop a file or click to browse — PDF · DOCX · TXT
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function NewComparisonPage() {
  const router = useRouter();
  const [title, setTitle] = React.useState("");
  const [oldText, setOldText] = React.useState("");
  const [newText, setNewText] = React.useState("");
  const [oldFile, setOldFile] = React.useState<File | null>(null);
  const [newFile, setNewFile] = React.useState<File | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const submit = async () => {
    if (submitting) return;
    const hasOld = oldFile || oldText.trim();
    const hasNew = newFile || newText.trim();
    if (!hasOld || !hasNew) {
      toast.error("Provide both an Original and a Revised version");
      return;
    }
    setSubmitting(true);
    try {
      const comparison = await createComparison({
        title: title.trim() || "Untitled comparison",
        old_file: oldFile ?? undefined,
        new_file: newFile ?? undefined,
        old_content: oldFile ? undefined : oldText,
        new_content: newFile ? undefined : newText,
      });
      toast.success("Comparison created");
      router.push(`/compare/${comparison.id}`);
    } catch (e) {
      toast.error(`Failed: ${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="New comparison"
        description="Upload or paste two versions of a document to see a word-level, side-by-side redline of what changed."
      />

      <div className="mb-6">
        <label className="micro-label mb-2 block">Title</label>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Smart Secure brochure v1 vs v2"
          className="h-10 max-w-md text-base"
        />
      </div>

      <div className="grid gap-8 md:grid-cols-2">
        <SideInput label="Original" text={oldText} setText={setOldText} file={oldFile} setFile={setOldFile} />
        <SideInput label="Revised" text={newText} setText={setNewText} file={newFile} setFile={setNewFile} />
      </div>

      <div className="mt-8 flex items-center gap-3">
        <Button onClick={submit} disabled={submitting} size="hero">
          {submitting ? "Comparing…" : "Compare →"}
        </Button>
        <Button asChild variant="ghost" size="hero">
          <Link href="/compare">Cancel</Link>
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/app/\(workspace\)/compare/new/page.tsx
git commit -m "feat: add new-comparison upload page"
```

---

## Task 10: Comparison detail/viewer page

**Files:**
- Create: `frontend/app/(workspace)/compare/[id]/page.tsx`

**Interfaces:**
- Consumes: `getComparison()` (Task 6), `DiffViewer` (Task 7).

- [ ] **Step 1: Write the page**

```tsx
// frontend/app/(workspace)/compare/[id]/page.tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getComparison } from "@/lib/api";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { DiffViewer } from "@/components/compare/DiffViewer";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

function comparisonStatusTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "processing") return "info" as const;
  return "danger" as const;
}

export default async function ComparisonDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let comparison;
  try {
    comparison = await getComparison(id);
  } catch {
    notFound();
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <Link
        href="/compare"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to comparisons
      </Link>
      <PageHeader
        title={comparison.title}
        meta={
          <>
            <PageHeaderMeta
              label="Status"
              value={
                <StatusPill tone={comparisonStatusTone(comparison.status)}>
                  {comparison.status}
                </StatusPill>
              }
            />
            <PageHeaderMeta label="Created" value={formatDate(comparison.created_at)} />
          </>
        }
      />

      {comparison.status === "failed" ? (
        <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 text-sm text-sev-critical">
          Comparison failed: {comparison.error_message ?? "Unknown error"}
        </div>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-4 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm bg-sev-critical/30" /> Removed
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm bg-success/30" /> Added
            </span>
          </div>
          <DiffViewer blocks={comparison.diff_result ?? []} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 3: Commit**

```bash
git add frontend/app/\(workspace\)/compare/\[id\]/page.tsx
git commit -m "feat: add comparison detail/viewer page"
```

---

## Task 11: Navigation — Sidebar + Command Palette

**Files:**
- Modify: `frontend/components/workspace/Sidebar.tsx`
- Modify: `frontend/components/workspace/CommandPalette.tsx`

**Interfaces:**
- Consumes: nothing new (pure UI wiring to routes created in Tasks 8-10).

- [ ] **Step 1: Add "Compare" to the Sidebar**

In `frontend/components/workspace/Sidebar.tsx`, add `GitCompare` to the `lucide-react` import and a new item to the `"Workspace"` section:

```typescript
// Change the lucide-react import:
import {
  FileText,
  PenSquare,
  Library,
  Sparkles,
  LineChart,
  Settings,
  Search,
  Boxes,
  GitCompare,
} from "lucide-react";

// Change the "Workspace" section's items array:
{
  title: "Workspace",
  items: [
    { label: "Submissions", href: "/", icon: <FileText className="h-3.5 w-3.5" />, kbd: "S" },
    { label: "New analysis", href: "/new", icon: <PenSquare className="h-3.5 w-3.5" />, kbd: "N" },
    { label: "Compare", href: "/compare", icon: <GitCompare className="h-3.5 w-3.5" />, kbd: "C" },
  ],
},
```

- [ ] **Step 2: Add "Compare" to the Command Palette**

In `frontend/components/workspace/CommandPalette.tsx`, add `GitCompare` to the `lucide-react` import and a new entry to `NAV`:

```typescript
// Change the lucide-react import:
import { FileText, PenSquare, Library, LineChart, Boxes, Settings, Search, GitCompare } from "lucide-react";

// Change the NAV array:
const NAV: Entry[] = [
  { id: "nav-submissions", label: "Submissions", href: "/", icon: <FileText className="h-4 w-4" /> },
  { id: "nav-new", label: "New analysis", sublabel: "Action", href: "/new", icon: <PenSquare className="h-4 w-4" /> },
  { id: "nav-compare", label: "Compare", sublabel: "Action", href: "/compare", icon: <GitCompare className="h-4 w-4" /> },
  { id: "nav-rules", label: "Rules", href: "/rules", icon: <Library className="h-4 w-4" /> },
  { id: "nav-dashboard", label: "Dashboard", href: "/dashboard", icon: <LineChart className="h-4 w-4" /> },
  { id: "nav-kb", label: "Knowledge base", href: "/knowledge-base", icon: <Boxes className="h-4 w-4" /> },
  { id: "nav-settings", label: "Project settings", href: "/settings", icon: <Settings className="h-4 w-4" /> },
];
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 4: Commit**

```bash
git add frontend/components/workspace/Sidebar.tsx frontend/components/workspace/CommandPalette.tsx
git commit -m "feat: add Compare nav entry to sidebar and command palette"
```

---

## Task 12: End-to-end manual verification

**Files:** none (verification only)

**Interfaces:** none

- [ ] **Step 1: Apply the migration to the local/dev database**

Run (from `backend/`, against a dev database the user has approved for this): `alembic upgrade head`
Expected: `document_comparisons` table created, no errors. (This step touches a real database — confirm with the user before running against anything other than a disposable local dev DB.)

- [ ] **Step 2: Start backend and frontend dev servers**

Follow this project's existing `run` skill / dev-server startup procedure (e.g. `docker-compose up` or the documented local dev flow) to bring up both the FastAPI backend and the Next.js frontend.

- [ ] **Step 3: Manually exercise the full flow in a browser**

- Navigate to `/compare` — confirm the empty state renders ("No comparisons yet").
- Click "New comparison", paste two clearly different short paragraphs as Original/Revised (e.g. "The quick brown fox jumps." vs "The quick red fox leaps."), give it a title, submit.
- Confirm redirect to `/compare/{id}` and that the diff view shows word-level highlighting (struck-through red words on the left, highlighted green words on the right) for the changed words only, with unchanged words plain.
- Go back to `/compare` and confirm the new comparison appears in the list with status "completed".
- Create a second comparison using one pasted side and one uploaded `.docx`/`.pdf` file to confirm the file-upload path works end to end.
- Confirm the "Compare" entry appears in the Sidebar and in the ⌘K command palette, and both navigate to `/compare`.

- [ ] **Step 4: Report results**

Note in this task's completion summary whether all sub-checks in Step 3 passed, and paste any errors encountered (console errors, failed requests) for follow-up.

---

## Self-Review Notes

- **Spec coverage:** Flow (Tasks 5-10), extraction (Task 3), diff algorithm (Task 4), data model (Task 1), migration (Task 2), API (Task 5), error handling — failed status path (Task 5's `try/except` + Task 10's failed-state UI), frontend UI incl. nav (Tasks 6-11), testing (Tasks 1, 3, 4, 5 backend; Tasks 6-11 typecheck + Task 12 manual) — all spec sections have a covered task.
- **Placeholder scan:** none found — every step has complete, runnable code.
- **Type consistency:** `DiffBlock`/`DiffWord` (Task 6) match the JSON shape produced by `build_diff`/`word_diff` (Task 4) and consumed by `DiffViewer` (Task 7) exactly (`type`, `old_text`/`new_text`, `old_words`/`new_words`, `text`/`changed`). `DocumentComparison` type (Task 6) matches `_serialize()`'s output (Task 5) field-for-field.
