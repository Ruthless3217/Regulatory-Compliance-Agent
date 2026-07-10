# Pixel-Faithful Document Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render both compared documents to page images and overlay word-level change highlights at their true coordinates (Draftable-style), keeping the existing text-diff view as a fallback.

**Architecture:** Every input is normalized to PDF (Word → Gotenberg/LibreOffice; PDF passthrough), rendered to PNG pages via `pypdfium2`, and its words extracted with coordinates via `pdfplumber`. The two word streams are aligned by the existing token engine; changed words become colored boxes stored (in PDF points) in a new `render_result` JSONB column. Rendering runs in a FastAPI BackgroundTask; the text diff stays synchronous and instant. The frontend shows a Pixel view (default for uploads) with a toggle to the Text view.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, `pdfplumber` + `pypdfium2` + `Pillow` (already installed), `httpx` (present), Gotenberg sidecar container, Next.js/React/TypeScript frontend.

## Global Constraints

- **No live LLM/API calls** anywhere in this feature. Tests must mock `httpx`, `pdfplumber`, and `pypdfium2` — never hit a real Gotenberg or render real pages in unit tests. (`feedback_no_live_api_calls`)
- **Do NOT run `git commit` automatically.** The user commits manually (`feedback_no_auto_commit`). The "Commit" steps below are checkpoints: stage the listed files with `git add`, then STOP and tell the user the checkpoint is ready. Never invoke `git commit`.
- **Backend runs in Docker with no code volume mount** — changes require `docker compose build backend`. Two `.env` files; every env var must be forwarded in each compose file. (`feedback_docker_env_ops`)
- **Coordinates are stored in PDF points** (origin top-left, matching `pdfplumber`'s `top`/`x0`). The frontend scales boxes as a percentage of page `w_pt`/`h_pt`.
- **Migration number is 0014** (next after `0013_document_comparisons`).
- **Box `type` is per side:** `removed` (red, on original pages) or `added` (green, on revised pages). A modified word emits both, linked by one `change_id`.
- Python: reuse existing helpers in `app/services/comparison_service.py` (`_norm_token`, `_is_placeholder`, `SequenceMatcher`); do not duplicate them.

---

## File Structure

**Backend (create):**
- `backend/app/services/gotenberg_client.py` — Word→PDF over HTTP.
- `backend/app/services/pdf_render_service.py` — normalize-to-PDF, render pages, extract positioned words.
- `backend/app/services/comparison_render.py` — orchestrator: build `render_result`, the BackgroundTask body.
- `backend/alembic/versions/0014_pixel_render_columns.py` — new columns.
- Tests: `backend/tests/services/test_gotenberg_client.py`, `test_pdf_render_service.py`, `test_comparison_render.py`, and additions to `test_comparison_cross_format.py`.

**Backend (modify):**
- `backend/app/config.py` — `gotenberg_url`, `pixel_render_page_cap`.
- `backend/app/models/document_comparison.py` — `render_result`, `render_status`, `render_error`.
- `backend/app/services/comparison_service.py` — add `word_level_ops`.
- `backend/app/api/routes/comparisons.py` — schedule render, serialize render fields, pages endpoint, delete cleanup.
- `backend/requirements.txt` — pin `pypdfium2`.

**Frontend (modify):**
- `frontend/lib/types.ts` — `RenderResult`, `RenderPage`, `RenderBox`, `RenderChange`; extend `DocumentComparison`.
- `frontend/lib/api.ts` — `comparisonPageImageUrl()` helper.
- `frontend/components/compare/PixelDiffViewer.tsx` — new pixel viewer (create).
- `frontend/components/compare/CompareWorkspace.tsx` — Pixel|Text toggle + client polling.
- `frontend/app/(workspace)/compare/[id]/page.tsx` — pass full comparison to the workspace.

**Deploy (modify):**
- `docker-compose.yml`, `docker-compose.prod.yml` — `gotenberg` service + `GOTENBERG_URL`.
- `scripts/deploy/build-and-save.sh` — pull+save `gotenberg/gotenberg:8`.

---

## Task 1: Config settings + pin pypdfium2

**Files:**
- Modify: `backend/app/config.py:194-195` (near `max_upload_size`/`upload_dir`)
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_config_pixel.py` (create)

**Interfaces:**
- Produces: `settings.gotenberg_url: str` (default `"http://gotenberg:3000"`), `settings.pixel_render_page_cap: int` (default `60`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_pixel.py`:
```python
from app.config import settings


def test_pixel_render_settings_have_sane_defaults():
    assert settings.gotenberg_url  # non-empty
    assert settings.gotenberg_url.startswith("http")
    assert settings.pixel_render_page_cap >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config_pixel.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'gotenberg_url'`

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, immediately after the `upload_dir` line (`upload_dir: str = "./uploads"`):
```python
    # --- Pixel-faithful comparison rendering ---
    # Gotenberg sidecar (wraps LibreOffice) used to convert Word -> PDF before
    # rendering pages. Internal compose DNS name; no API key.
    gotenberg_url: str = "http://gotenberg:3000"
    # Max pages rendered per side; surplus is reported as truncated_pages, never
    # silently dropped.
    pixel_render_page_cap: int = 60
```

- [ ] **Step 4: Pin pypdfium2**

In `backend/requirements.txt`, on the line after `pdfplumber==0.11.9`, add (pin to the version already resolved transitively — verify with `python -c "import pypdfium2, importlib.metadata as m; print(m.version('pypdfium2'))"` and use that exact value):
```
pypdfium2==4.30.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_config_pixel.py -v`
Expected: PASS

- [ ] **Step 6: Commit checkpoint** (stage only — do not commit)

```bash
git add backend/app/config.py backend/requirements.txt backend/tests/test_config_pixel.py
# STOP: tell the user this checkpoint is ready to commit.
```

---

## Task 2: DB migration 0014 + model columns

**Files:**
- Create: `backend/alembic/versions/0014_pixel_render_columns.py`
- Modify: `backend/app/models/document_comparison.py`
- Test: `backend/tests/models/test_document_comparison_render_columns.py` (create)

**Interfaces:**
- Produces: `DocumentComparison.render_result` (JSONB, nullable), `.render_status` (String(50), default `"processing"`), `.render_error` (Text, nullable).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/models/test_document_comparison_render_columns.py`:
```python
from app.models.document_comparison import DocumentComparison


def test_model_exposes_render_columns():
    cols = DocumentComparison.__table__.columns.keys()
    assert "render_result" in cols
    assert "render_status" in cols
    assert "render_error" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/models/test_document_comparison_render_columns.py -v`
Expected: FAIL (`assert 'render_result' in [...]`)

- [ ] **Step 3: Add columns to the model**

In `backend/app/models/document_comparison.py`, after the `error_message` column line, add:
```python
    # --- Pixel-faithful render (2026-07-07) ---
    render_result = Column(JSONB, nullable=True)          # overlay model: pages+boxes+changes
    render_status = Column(String(50), nullable=False, default="processing")  # processing|completed|failed|skipped
    render_error = Column(Text, nullable=True)
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0014_pixel_render_columns.py`:
```python
"""pixel render columns — page-image overlay model for the Compare tool.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_comparisons", sa.Column("render_result", postgresql.JSONB(), nullable=True))
    op.add_column(
        "document_comparisons",
        sa.Column("render_status", sa.String(length=50), nullable=False, server_default="processing"),
    )
    op.add_column("document_comparisons", sa.Column("render_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_comparisons", "render_error")
    op.drop_column("document_comparisons", "render_status")
    op.drop_column("document_comparisons", "render_result")
```

- [ ] **Step 5: Run the model test + syntax-check the migration**

Run: `cd backend && python -m pytest tests/models/test_document_comparison_render_columns.py -v && python -m py_compile alembic/versions/0014_pixel_render_columns.py && echo "migration compiles"`
Expected: model test PASS; prints `migration compiles`. (The migration is actually applied against Postgres in Task 12's stack bring-up.)

- [ ] **Step 6: Commit checkpoint** (stage only)

```bash
git add backend/app/models/document_comparison.py backend/alembic/versions/0014_pixel_render_columns.py backend/tests/models/test_document_comparison_render_columns.py
# STOP for user commit.
```

---

## Task 3: Gotenberg client (Word → PDF)

**Files:**
- Create: `backend/app/services/gotenberg_client.py`
- Test: `backend/tests/services/test_gotenberg_client.py`

**Interfaces:**
- Produces: `convert_to_pdf(src_path: str, *, timeout: float = 120.0) -> bytes` — posts the file to Gotenberg, returns PDF bytes. Raises `GotenbergError` on any non-200 / transport error.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/test_gotenberg_client.py`:
```python
from unittest.mock import patch, MagicMock
import pytest

from app.services.gotenberg_client import convert_to_pdf, GotenbergError


def _resp(status=200, content=b"%PDF-1.7 fake"):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.text = content.decode("latin-1")
    return r


def test_convert_to_pdf_posts_file_and_returns_bytes(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"fake docx")
    posted = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, files=None, **k):
            posted["url"] = url
            posted["names"] = list(files.keys()) if files else []
            return _resp()

    with patch("app.services.gotenberg_client.httpx.Client", FakeClient):
        out = convert_to_pdf(str(src))

    assert out.startswith(b"%PDF")
    assert posted["url"].endswith("/forms/libreoffice/convert")


def test_convert_to_pdf_raises_on_non_200(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): return _resp(status=503, content=b"busy")

    with patch("app.services.gotenberg_client.httpx.Client", FakeClient):
        with pytest.raises(GotenbergError):
            convert_to_pdf(str(src))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_gotenberg_client.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.gotenberg_client`)

- [ ] **Step 3: Write the client**

Create `backend/app/services/gotenberg_client.py`:
```python
"""Thin client for the Gotenberg sidecar — converts office documents to PDF via
its LibreOffice route. Synchronous (called from a threadpool background task)."""
import os
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class GotenbergError(RuntimeError):
    """Raised when Gotenberg is unreachable or returns a non-200 response."""


def convert_to_pdf(src_path: str, *, timeout: float = 120.0) -> bytes:
    """POST `src_path` to Gotenberg's LibreOffice route; return the PDF bytes."""
    url = settings.gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    filename = os.path.basename(src_path)
    try:
        with httpx.Client(timeout=timeout) as client:
            with open(src_path, "rb") as fh:
                resp = client.post(url, files={"files": (filename, fh)})
    except httpx.HTTPError as e:  # transport/timeout
        raise GotenbergError(f"Gotenberg request failed: {e}") from e
    if resp.status_code != 200:
        raise GotenbergError(f"Gotenberg returned {resp.status_code}: {resp.text[:200]}")
    return resp.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_gotenberg_client.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit checkpoint** (stage only)

```bash
git add backend/app/services/gotenberg_client.py backend/tests/services/test_gotenberg_client.py
# STOP for user commit.
```

---

## Task 4: PDF render service (to_pdf, render_pages, positioned_words)

**Files:**
- Create: `backend/app/services/pdf_render_service.py`
- Test: `backend/tests/services/test_pdf_render_service.py`

**Interfaces:**
- Consumes: `gotenberg_client.convert_to_pdf` (Task 3); `comparison_service._detect_running_lines`, `_PAGE_NUMBER` (existing).
- Produces:
  - `@dataclass PositionedWord(text: str, page: int, x0: float, y0: float, x1: float, y1: float)`
  - `@dataclass PageMeta(n: int, w_pt: float, h_pt: float, image_path: str)`
  - `to_pdf(file_path: str, content_type: str, out_dir: str, side: str) -> str` — returns a path to a PDF (passthrough for pdf; Gotenberg for docx). Raises `ValueError` for non-renderable types (text).
  - `render_pages(pdf_path: str, out_dir: str, cap: int) -> tuple[list[PageMeta], int]` — writes `page-0001.png`… into `out_dir`; returns `(metas, truncated_pages)`.
  - `positioned_words(pdf_path: str) -> list[PositionedWord]` — reading-order words with running-header/footer + page-number lines removed.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/services/test_pdf_render_service.py`:
```python
from unittest.mock import patch, MagicMock
import pytest

from app.services.pdf_render_service import (
    PositionedWord, PageMeta, to_pdf, positioned_words,
)


def _mock_pdf(pages_words):
    """pages_words: list of list of (text, x0, top, x1, bottom)."""
    pages = []
    for words in pages_words:
        p = MagicMock()
        p.extract_words.return_value = [
            {"text": t, "x0": x0, "top": top, "x1": x1, "bottom": bot}
            for (t, x0, top, x1, bot) in words
        ]
        p.width, p.height = 595, 842
        pages.append(p)
    pdf = MagicMock()
    pdf.__enter__.return_value.pages = pages
    pdf.__exit__.return_value = False
    return pdf


def test_positioned_words_returns_coordinates(tmp_path):
    words = [[("Hello", 10, 20, 40, 32), ("world.", 42, 20, 80, 32)]]
    with patch("pdfplumber.open", return_value=_mock_pdf(words)):
        out = positioned_words("x.pdf")
    assert out[0] == PositionedWord("Hello", 1, 10, 20, 40, 32)
    assert out[1].text == "world."


def test_positioned_words_strips_repeated_running_header(tmp_path):
    hdr = ("UIN:116N198V08", 10, 5, 120, 15)
    pages = [[hdr, ("Body", 10, 40, 40, 52)] for _ in range(4)]
    with patch("pdfplumber.open", return_value=_mock_pdf(pages)):
        out = positioned_words("x.pdf")
    assert all(w.text != "UIN:116N198V08" for w in out)
    assert any(w.text == "Body" for w in out)


def test_to_pdf_passthrough_for_pdf(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.7")
    assert to_pdf(str(src), "pdf", str(tmp_path), "old") == str(src)


def test_to_pdf_converts_docx_via_gotenberg(tmp_path):
    src = tmp_path / "a.docx"
    src.write_bytes(b"docx")
    with patch("app.services.pdf_render_service.convert_to_pdf", return_value=b"%PDF-1.7 x"):
        out = to_pdf(str(src), "docx", str(tmp_path), "old")
    assert out.endswith("old-source.pdf")
    assert open(out, "rb").read().startswith(b"%PDF")


def test_to_pdf_rejects_text():
    with pytest.raises(ValueError):
        to_pdf("", "text", "/tmp", "old")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_pdf_render_service.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.pdf_render_service`)

- [ ] **Step 3: Write the service**

Create `backend/app/services/pdf_render_service.py`:
```python
"""Normalize any comparable document to PDF, render its pages to PNG, and extract
its words with coordinates (PDF points, top-left origin) for the pixel-faithful
Compare view. No LLM calls."""
import os
import logging
from dataclasses import dataclass
from typing import List, Tuple

from app.services.gotenberg_client import convert_to_pdf
from app.services.comparison_service import _detect_running_lines, _PAGE_NUMBER

logger = logging.getLogger(__name__)


@dataclass
class PositionedWord:
    text: str
    page: int          # 1-based
    x0: float
    y0: float          # top
    x1: float
    y1: float          # bottom


@dataclass
class PageMeta:
    n: int             # 1-based
    w_pt: float
    h_pt: float
    image_path: str


def to_pdf(file_path: str, content_type: str, out_dir: str, side: str) -> str:
    """Return a path to a PDF representation of the input.

    pdf -> passthrough; docx -> Gotenberg (written to <out_dir>/<side>-source.pdf).
    text/other -> ValueError (no layout to render)."""
    if content_type == "pdf":
        return file_path
    if content_type == "docx":
        os.makedirs(out_dir, exist_ok=True)
        pdf_bytes = convert_to_pdf(file_path)
        dest = os.path.join(out_dir, f"{side}-source.pdf")
        with open(dest, "wb") as fh:
            fh.write(pdf_bytes)
        return dest
    raise ValueError(f"content_type {content_type!r} has no page layout to render")


def render_pages(pdf_path: str, out_dir: str, cap: int) -> Tuple[List[PageMeta], int]:
    """Render up to `cap` pages of `pdf_path` to PNGs in `out_dir`.

    Returns (metas, truncated_pages). Image files are named page-0001.png…"""
    import pypdfium2 as pdfium

    os.makedirs(out_dir, exist_ok=True)
    metas: List[PageMeta] = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        total = len(pdf)
        n_render = min(total, cap)
        for i in range(n_render):
            page = pdf[i]
            # scale 2.0 ≈ 144 DPI — crisp without being huge.
            bitmap = page.render(scale=2.0)
            pil = bitmap.to_pil()
            image_path = os.path.join(out_dir, f"page-{i + 1:04d}.png")
            pil.save(image_path)
            w_pt, h_pt = page.get_size()   # points (1/72 inch)
            metas.append(PageMeta(n=i + 1, w_pt=float(w_pt), h_pt=float(h_pt), image_path=image_path))
        return metas, max(0, total - n_render)
    finally:
        pdf.close()


def positioned_words(pdf_path: str) -> List[PositionedWord]:
    """Words in reading order with bboxes (PDF points), running headers/footers
    and page-number lines removed (same policy as the text extractor)."""
    import pdfplumber

    page_lines: List[List[str]] = []
    raw: List[List[dict]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words() or []
            raw.append(words)
            # group words into visual lines (by rounded top) to reuse running-line detection
            lines: dict = {}
            for w in words:
                lines.setdefault(round(w["top"]), []).append(w["text"])
            page_lines.append([" ".join(v) for v in lines.values()])

    running = _detect_running_lines(page_lines)

    out: List[PositionedWord] = []
    for pi, words in enumerate(raw):
        # rebuild per-line text to drop whole running/page-number lines
        by_top: dict = {}
        for w in words:
            by_top.setdefault(round(w["top"]), []).append(w)
        for top_key, line_words in by_top.items():
            line_text = " ".join(w["text"] for w in line_words)
            if line_text in running or _PAGE_NUMBER.match(line_text):
                continue
            for w in line_words:
                out.append(PositionedWord(
                    text=w["text"], page=pi + 1,
                    x0=float(w["x0"]), y0=float(w["top"]),
                    x1=float(w["x1"]), y1=float(w["bottom"]),
                ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_pdf_render_service.py -v`
Expected: PASS (5 tests). `render_pages` is not unit-tested here (needs a real PDF render); it is exercised in Task 6's integration note.

- [ ] **Step 5: Commit checkpoint** (stage only)

```bash
git add backend/app/services/pdf_render_service.py backend/tests/services/test_pdf_render_service.py
# STOP for user commit.
```

---

## Task 5: `word_level_ops` in comparison_service

**Files:**
- Modify: `backend/app/services/comparison_service.py` (add at end)
- Test: `backend/tests/services/test_comparison_cross_format.py` (append)

**Interfaces:**
- Consumes: existing `_norm_token`, `_is_placeholder` (both in this module).
- Produces: `word_level_ops(old_texts: List[str], new_texts: List[str]) -> tuple[list[dict], list[dict], list[dict]]` returning `(old_marks, new_marks, changes)`:
  - `old_marks`: `[{"index": i, "type": "removed"|"changed", "change_id": str}]` for each non-equal old word.
  - `new_marks`: `[{"index": j, "type": "added"|"changed", "change_id": str}]` for each non-equal new word.
  - `changes`: `[{"id": str, "kind": "removed"|"added"|"modified", "old_text": str, "new_text": str}]`.
  - Placeholder fills (`<XX>`→value) are treated as equal (no marks, no change).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/services/test_comparison_cross_format.py`:
```python
from app.services.comparison_service import word_level_ops


def test_word_level_ops_flags_only_changed_word():
    old = ["The", "sum", "is", "fixed."]
    new = ["The", "sum", "is", "variable."]
    old_marks, new_marks, changes = word_level_ops(old, new)
    assert [m["index"] for m in old_marks] == [3]
    assert old_marks[0]["type"] == "changed"
    assert [m["index"] for m in new_marks] == [3]
    assert changes[0]["kind"] == "modified"
    assert changes[0]["old_text"] == "fixed."
    assert changes[0]["new_text"] == "variable."
    # both sides link to the same change
    assert old_marks[0]["change_id"] == new_marks[0]["change_id"] == changes[0]["id"]


def test_word_level_ops_pure_insert_and_delete():
    old = ["Keep.", "Gone."]
    new = ["Keep.", "Fresh."]
    old_marks, new_marks, changes = word_level_ops(old, new)
    assert old_marks[0]["type"] == "changed"   # Gone. -> Fresh. is a modification at same slot
    assert new_marks[0]["type"] == "changed"


def test_word_level_ops_suppresses_placeholder_fill():
    old = ["Pay", "for", "<XX>", "years."]
    new = ["Pay", "for", "10", "years."]
    old_marks, new_marks, changes = word_level_ops(old, new)
    assert old_marks == []
    assert new_marks == []
    assert changes == []


def test_word_level_ops_added_only():
    old = ["Alpha."]
    new = ["Alpha.", "Beta", "added."]
    old_marks, new_marks, changes = word_level_ops(old, new)
    assert old_marks == []
    assert {m["type"] for m in new_marks} == {"added"}
    assert changes[0]["kind"] == "added"
    assert changes[0]["new_text"] == "Beta added."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_comparison_cross_format.py -k word_level_ops -v`
Expected: FAIL (`ImportError: cannot import name 'word_level_ops'`)

- [ ] **Step 3: Implement `word_level_ops`**

Append to `backend/app/services/comparison_service.py`:
```python
def word_level_ops(old_texts: List[str], new_texts: List[str]):
    """Align two positioned-word streams by normalized text and tag each word.

    Returns (old_marks, new_marks, changes). Placeholder fills (e.g. '<XX>' filled
    with a value) are treated as equal. Used by the pixel-faithful overlay; shares
    the token normalizer with `build_diff`.
    """
    o_norm = [_norm_token(t) for t in old_texts]
    n_norm = [_norm_token(t) for t in new_texts]
    matcher = SequenceMatcher(None, o_norm, n_norm, autojunk=False)

    old_marks: List[dict] = []
    new_marks: List[dict] = []
    changes: List[dict] = []
    counter = 0

    def _all_ph(texts):
        return bool(texts) and all(_is_placeholder(t) for t in texts)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_span = old_texts[i1:i2]
        new_span = new_texts[j1:j2]
        # placeholder fill on either side => expected, not an edit
        if _all_ph(old_span) or _all_ph(new_span):
            continue
        cid = f"r{counter}"
        counter += 1
        if tag == "delete":
            kind, otype, ntype = "removed", "removed", None
        elif tag == "insert":
            kind, otype, ntype = "added", None, "added"
        else:  # replace
            kind, otype, ntype = "modified", "changed", "changed"
        for k in range(i1, i2):
            old_marks.append({"index": k, "type": otype, "change_id": cid})
        for k in range(j1, j2):
            new_marks.append({"index": k, "type": ntype, "change_id": cid})
        changes.append({
            "id": cid,
            "kind": kind,
            "old_text": " ".join(old_span),
            "new_text": " ".join(new_span),
        })
    return old_marks, new_marks, changes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_comparison_cross_format.py -k word_level_ops -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole comparison suite (no regressions)**

Run: `cd backend && python -m pytest tests/services/test_comparison_cross_format.py tests/services/test_comparison_service.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit checkpoint** (stage only)

```bash
git add backend/app/services/comparison_service.py backend/tests/services/test_comparison_cross_format.py
# STOP for user commit.
```

---

## Task 6: Overlay builder + render orchestrator

**Files:**
- Create: `backend/app/services/comparison_render.py`
- Test: `backend/tests/services/test_comparison_render.py`

**Interfaces:**
- Consumes: `pdf_render_service` (Task 4), `word_level_ops` (Task 5), `PositionedWord`/`PageMeta`.
- Produces:
  - `build_overlay(old_words, new_words, old_pages, new_pages) -> dict` — the `render_result` JSON (pure function, no I/O).
  - `render_comparison(comparison_id: str) -> None` — BackgroundTask body: opens its own DB session, renders both sides, saves `render_result`/`render_status`.

- [ ] **Step 1: Write the failing test for `build_overlay`** (pure, fully unit-testable)

Create `backend/tests/services/test_comparison_render.py`:
```python
from app.services.pdf_render_service import PositionedWord, PageMeta
from app.services.comparison_render import build_overlay


def _pw(text, page, x0, y0, x1, y1):
    return PositionedWord(text, page, x0, y0, x1, y1)


def test_build_overlay_boxes_changed_words_on_each_side():
    old_words = [_pw("The", 1, 0, 0, 10, 8), _pw("fixed.", 1, 12, 0, 30, 8)]
    new_words = [_pw("The", 1, 0, 0, 10, 8), _pw("variable.", 1, 12, 0, 40, 8)]
    old_pages = [PageMeta(1, 100, 200, "/x/old/page-0001.png")]
    new_pages = [PageMeta(1, 100, 200, "/x/new/page-0001.png")]

    overlay = build_overlay(old_words, new_words, old_pages, new_pages)

    assert overlay["old"]["pages"][0]["w_pt"] == 100
    old_boxes = overlay["old"]["pages"][0]["boxes"]
    new_boxes = overlay["new"]["pages"][0]["boxes"]
    assert len(old_boxes) == 1 and old_boxes[0]["type"] == "removed"
    assert old_boxes[0]["x0"] == 12  # the "fixed." box
    assert len(new_boxes) == 1 and new_boxes[0]["type"] == "added"
    # one modified change, linking a box on each side
    assert len(overlay["changes"]) == 1
    ch = overlay["changes"][0]
    assert ch["kind"] == "modified"
    assert ch["old"]["text"] == "fixed." and ch["new"]["text"] == "variable."
    assert old_boxes[0]["change_id"] == ch["id"] == new_boxes[0]["change_id"]


def test_build_overlay_no_changes_is_empty():
    words = [_pw("Same.", 1, 0, 0, 10, 8)]
    pages = [PageMeta(1, 100, 200, "p")]
    overlay = build_overlay(words, list(words), pages, pages)
    assert overlay["changes"] == []
    assert overlay["old"]["pages"][0]["boxes"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_comparison_render.py -v`
Expected: FAIL (`ModuleNotFoundError: app.services.comparison_render`)

- [ ] **Step 3: Implement `build_overlay` and `render_comparison`**

Create `backend/app/services/comparison_render.py`:
```python
"""Pixel-faithful Compare overlay: run both documents through render + word
extraction, align words, and produce the `render_result` overlay model. Also the
BackgroundTask body that persists it."""
import os
import logging
from typing import List

from app.config import settings
from app.database import SessionLocal
from app.models.document_comparison import DocumentComparison
from app.services.comparison_service import word_level_ops
from app.services import pdf_render_service as prs

logger = logging.getLogger(__name__)


def _side_model(words: List[prs.PositionedWord], pages: List[prs.PageMeta], marks: List[dict]) -> dict:
    """Assemble one side's pages with boxes from word marks."""
    by_change_first: dict = {}
    pages_out = {p.n: {"n": p.n, "w_pt": p.w_pt, "h_pt": p.h_pt, "boxes": []} for p in pages}
    for m in marks:
        w = words[m["index"]]
        if w.page not in pages_out:
            continue  # word on a page past the render cap
        box = {"x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1,
               "type": m["type"], "change_id": m["change_id"]}
        pages_out[w.page]["boxes"].append(box)
        by_change_first.setdefault(m["change_id"], {"page": w.page, "bbox": [w.x0, w.y0, w.x1, w.y1]})
    return {"pages": [pages_out[k] for k in sorted(pages_out)]}, by_change_first


def build_overlay(old_words, new_words, old_pages, new_pages, truncated_pages: int = 0) -> dict:
    """Pure overlay builder. Aligns the two word streams and boxes each change."""
    old_texts = [w.text for w in old_words]
    new_texts = [w.text for w in new_words]
    old_marks, new_marks, changes = word_level_ops(old_texts, new_texts)

    old_side, old_first = _side_model(old_words, old_pages, old_marks)
    new_side, new_first = _side_model(new_words, new_pages, new_marks)

    changes_out = []
    for ch in changes:
        entry = {"id": ch["id"], "kind": ch["kind"]}
        if ch["id"] in old_first:
            entry["old"] = {**old_first[ch["id"]], "text": ch["old_text"]}
        if ch["id"] in new_first:
            entry["new"] = {**new_first[ch["id"]], "text": ch["new_text"]}
        changes_out.append(entry)

    return {"old": old_side, "new": new_side, "changes": changes_out,
            "truncated_pages": truncated_pages}


def _render_side(file_path, content_type, out_dir, side):
    pdf_path = prs.to_pdf(file_path, content_type, out_dir, side)
    pages, truncated = prs.render_pages(pdf_path, out_dir, settings.pixel_render_page_cap)
    words = prs.positioned_words(pdf_path)
    return words, pages, truncated


def render_comparison(comparison_id: str) -> None:
    """BackgroundTask: render both sides, build the overlay, persist it."""
    db = SessionLocal()
    try:
        c = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
        if not c:
            return
        # No layout to render if either side is pasted text / .txt.
        if not (c.old_file_path and c.new_file_path
                and c.old_content_type in ("docx", "pdf")
                and c.new_content_type in ("docx", "pdf")):
            c.render_status = "skipped"
            db.commit()
            return
        base = os.path.join(settings.upload_dir, str(c.id))
        old_words, old_pages, old_trunc = _render_side(
            c.old_file_path, c.old_content_type, os.path.join(base, "old"), "old")
        new_words, new_pages, new_trunc = _render_side(
            c.new_file_path, c.new_content_type, os.path.join(base, "new"), "new")
        c.render_result = build_overlay(
            old_words, new_words, old_pages, new_pages, old_trunc + new_trunc)
        c.render_status = "completed"
        db.commit()
    except Exception as e:  # noqa: BLE001 — fail closed, keep text view working
        logger.exception("Pixel render failed for %s", comparison_id)
        db.rollback()
        c = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
        if c:
            c.render_status = "failed"
            c.render_error = str(e)[:500]
            db.commit()
    finally:
        db.close()
```

Note: `_side_model` returns a tuple; `build_overlay` unpacks it (`old_side, old_first = _side_model(...)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_comparison_render.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Integration smoke (manual, real render — NOT a unit test)**

Run this one-off to confirm `render_pages` + `positioned_words` work on a real PDF (uses the repo's sample; no network):
```bash
cd backend && python -c "
from app.services import pdf_render_service as prs
import tempfile, os
d = tempfile.mkdtemp()
metas, trunc = prs.render_pages('../eTouch II_PD_V08.pdf', d, 3)
print('pages rendered:', len(metas), 'first size pt:', metas[0].w_pt, metas[0].h_pt)
print('image exists:', os.path.exists(metas[0].image_path))
print('words:', len(prs.positioned_words('../eTouch II_PD_V08.pdf')))
"
```
Expected: prints 3 pages, a real page size, `image exists: True`, and a non-zero word count.

- [ ] **Step 6: Commit checkpoint** (stage only)

```bash
git add backend/app/services/comparison_render.py backend/tests/services/test_comparison_render.py
# STOP for user commit.
```

---

## Task 7: API wiring — schedule render, serialize, serve pages, cleanup

**Files:**
- Modify: `backend/app/api/routes/comparisons.py`
- Test: `backend/tests/services/test_comparison_serialize.py` (create)

**Interfaces:**
- Consumes: `render_comparison` (Task 6).
- Produces: `POST /comparisons` (mounted at `/comparisons`, no `/api` prefix at the FastAPI layer) schedules the render + returns `render_status`; `GET /comparisons/{id}` returns `render_status`+`render_result`; `GET /comparisons/{id}/pages/{side}/{n}` streams a PNG; `DELETE` removes the image dir.

> **Why no TestClient tests here:** this repo has no API test harness (no `tests/api/`, no `get_db` override) and `DocumentComparison` uses Postgres `JSONB`/`UUID`, so a SQLite-backed unit test won't work. We TDD the DB-free `_serialize` helper; the endpoints (schedule, pages, delete) are exercised in Task 12's live end-to-end against the real stack.

- [ ] **Step 1: Write the failing test** (DB-free — constructs an in-memory model instance)

Create `backend/tests/services/test_comparison_serialize.py`:
```python
from app.models.document_comparison import DocumentComparison
from app.api.routes.comparisons import _serialize


def _make():
    c = DocumentComparison(
        title="t", old_content_type="docx", new_content_type="pdf",
        status="completed",
    )
    c.render_status = "processing"
    c.render_result = {"old": {"pages": []}, "new": {"pages": []}, "changes": [], "truncated_pages": 0}
    return c


def test_serialize_includes_render_status_always():
    data = _serialize(_make(), include_diff=False)
    assert data["render_status"] == "processing"
    assert "render_result" not in data  # only with include_diff


def test_serialize_includes_render_result_with_diff():
    data = _serialize(_make(), include_diff=True)
    assert data["render_result"]["truncated_pages"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/services/test_comparison_serialize.py -v`
Expected: FAIL (`KeyError: 'render_status'` — `_serialize` doesn't emit it yet).

- [ ] **Step 3: Wire the route**

In `backend/app/api/routes/comparisons.py`:

3a. Add imports at top:
```python
from fastapi import BackgroundTasks
from fastapi.responses import FileResponse
from app.services.comparison_render import render_comparison
```

3b. Extend `_serialize` to always include render fields:
```python
        "status": c.status,
        "error_message": c.error_message,
        "render_status": c.render_status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if include_diff:
        data["diff_result"] = c.diff_result
        data["render_result"] = c.render_result
    return data
```

3c. In `create_comparison`, add `background_tasks: BackgroundTasks` to the signature (after `db`), and after `db.refresh(comparison)` decide render status + schedule:
```python
    # Pixel render only when BOTH sides are renderable files.
    renderable = (
        comparison.status == "completed"
        and comparison.old_file_path and comparison.new_file_path
        and comparison.old_content_type in ("docx", "pdf")
        and comparison.new_content_type in ("docx", "pdf")
    )
    if renderable:
        comparison.render_status = "processing"
        db.commit()
        background_tasks.add_task(render_comparison, str(comparison.id))
    else:
        comparison.render_status = "skipped"
        db.commit()

    return _serialize(comparison, include_diff=True)
```

3d. Add the pages endpoint (after `get_comparison`):
```python
@router.get("/{comparison_id}/pages/{side}/{n}")
async def get_comparison_page(comparison_id: str, side: str, n: int, db: Session = Depends(get_db)):
    """Stream a rendered page image (PNG) for the pixel-faithful view."""
    if side not in ("old", "new"):
        raise HTTPException(status_code=400, detail="side must be 'old' or 'new'")
    path = os.path.join(settings.upload_dir, comparison_id, side, f"page-{n:04d}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(path, media_type="image/png")
```

3e. In `delete_comparison`, before `db.delete(comparison)`, also remove the image dir:
```python
    import shutil
    render_dir = os.path.join(settings.upload_dir, str(comparison.id))
    if os.path.isdir(render_dir):
        shutil.rmtree(render_dir, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/services/test_comparison_serialize.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend comparison suite (no regressions)**

Run: `cd backend && python -m pytest tests/services -k "compar or serialize" -q`
Expected: PASS

- [ ] **Step 6: Commit checkpoint** (stage only)

```bash
git add backend/app/api/routes/comparisons.py backend/tests/services/test_comparison_serialize.py
# STOP for user commit.
```

---

## Task 8: Deploy wiring — Gotenberg service, env, release script

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`, `scripts/deploy/build-and-save.sh`
- No automated test (infra) — verified by `docker compose config` + a live smoke.

- [ ] **Step 1: Add the Gotenberg service to `docker-compose.yml`**

Under `services:`, add:
```yaml
  gotenberg:
    image: gotenberg/gotenberg:8
    container_name: compliance-gotenberg
    restart: unless-stopped
    # Internal only — never publish to the host.
    command:
      - "gotenberg"
      - "--api-timeout=180s"
    mem_limit: 1g
```

- [ ] **Step 2: Forward `GOTENBERG_URL` to the backend** in `docker-compose.yml`

In the `backend.environment:` block add:
```yaml
      GOTENBERG_URL: ${GOTENBERG_URL:-http://gotenberg:3000}
```
And add `gotenberg` to the backend `depends_on:` list.

- [ ] **Step 3: Mirror both edits in `docker-compose.prod.yml`**

Add the same `gotenberg` service block and the `GOTENBERG_URL` env line + `depends_on` entry (prod uses `image:` refs, which the Gotenberg block already is).

- [ ] **Step 4: Add Gotenberg to the offline release** in `scripts/deploy/build-and-save.sh`

Next to the other infra image vars (`PG_IMAGE`, `REDIS_IMAGE`, `PG_BACKUP_IMAGE`), add:
```bash
GOTENBERG_IMAGE="gotenberg/gotenberg:8"
```
In the "Pulling infra images" section, add `pull_with_retry "${GOTENBERG_IMAGE}"`, and include `${GOTENBERG_IMAGE}` in the `docker save` list so it becomes a `dist/*.tar.gz`. Update the header comment count ("FIVE" → "SIX").

- [ ] **Step 5: Validate compose parses**

Run: `docker compose -f docker-compose.yml config >/dev/null && echo OK`
Expected: `OK` (no YAML errors).

- [ ] **Step 6: Live smoke (dev machine with internet)**

```bash
docker compose up -d gotenberg
docker compose build backend && docker compose up -d backend
docker compose exec backend python -c "from app.services.gotenberg_client import convert_to_pdf; import tempfile,docx; p=tempfile.mktemp(suffix='.docx'); d=docx.Document(); d.add_paragraph('hi'); d.save(p); print('pdf bytes:', len(convert_to_pdf(p)))"
```
Expected: prints a non-zero `pdf bytes:` count (backend reached Gotenberg and got a PDF).

- [ ] **Step 7: Commit checkpoint** (stage only)

```bash
git add docker-compose.yml docker-compose.prod.yml scripts/deploy/build-and-save.sh
# STOP for user commit.
```

---

## Task 9: Frontend types + page-image URL helper

**Files:**
- Modify: `frontend/lib/types.ts`, `frontend/lib/api.ts`

**Interfaces:**
- Produces: `RenderBox`, `RenderPage`, `RenderChange`, `RenderResult` types; `DocumentComparison` gains `render_status`/`render_result`; `comparisonPageImageUrl(id, side, n)`.

- [ ] **Step 1: Add types** to `frontend/lib/types.ts` (after `DiffBlock`):
```typescript
export type RenderBoxType = "removed" | "added";

export interface RenderBox {
  x0: number; y0: number; x1: number; y1: number;
  type: RenderBoxType;
  change_id: string;
}
export interface RenderPage { n: number; w_pt: number; h_pt: number; boxes: RenderBox[]; }
export interface RenderChangeRef { page: number; bbox: [number, number, number, number]; text: string; }
export interface RenderChange {
  id: string;
  kind: ChangeKind;
  old?: RenderChangeRef;
  new?: RenderChangeRef;
}
export interface RenderResult {
  old: { pages: RenderPage[] };
  new: { pages: RenderPage[] };
  changes: RenderChange[];
  truncated_pages: number;
}
export type RenderStatus = "processing" | "completed" | "failed" | "skipped";
```

- [ ] **Step 2: Extend `DocumentComparison`** in `frontend/lib/types.ts`:
```typescript
  diff_result?: DiffBlock[] | null;
  render_status?: RenderStatus | null;
  render_result?: RenderResult | null;
  created_at: string;
```

- [ ] **Step 3: Add the image URL helper** in `frontend/lib/api.ts` (in the comparisons section):
```typescript
export function comparisonPageImageUrl(id: string, side: "old" | "new", n: number): string {
  return `${base()}/comparisons/${id}/pages/${side}/${n}`;
}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 5: Commit checkpoint** (stage only)

```bash
git add frontend/lib/types.ts frontend/lib/api.ts
# STOP for user commit.
```

---

## Task 10: PixelDiffViewer component

**Files:**
- Create: `frontend/components/compare/PixelDiffViewer.tsx`

**Interfaces:**
- Consumes: `RenderResult`, `comparisonPageImageUrl` (Task 9).
- Produces: `<PixelDiffViewer comparisonId render selectedId onSelect />`.

- [ ] **Step 1: Write the component**

Create `frontend/components/compare/PixelDiffViewer.tsx`:
```tsx
"use client";
import * as React from "react";
import type { RenderResult, RenderPage } from "@/lib/types";
import { comparisonPageImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

export const pixelBoxDomId = (changeId: string, side: "old" | "new") => `pxl-${side}-${changeId}`;

interface Props {
  comparisonId: string;
  render: RenderResult;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}

export function PixelDiffViewer({ comparisonId, render, selectedId = null, onSelect }: Props) {
  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!selectedId || !scrollRef.current) return;
    const el =
      scrollRef.current.querySelector<HTMLElement>(`#${CSS.escape(pixelBoxDomId(selectedId, "old"))}`) ||
      scrollRef.current.querySelector<HTMLElement>(`#${CSS.escape(pixelBoxDomId(selectedId, "new"))}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedId]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
      <div className="grid grid-cols-2 border-b border-border bg-muted/30 text-xs">
        <div className="border-r border-border px-4 py-2 micro-label">Original</div>
        <div className="px-4 py-2 micro-label">Revised</div>
      </div>
      <div ref={scrollRef} className="grid max-h-[70vh] grid-cols-2 overflow-y-auto">
        <SideColumn comparisonId={comparisonId} side="old" pages={render.old.pages}
          selectedId={selectedId} onSelect={onSelect} />
        <SideColumn comparisonId={comparisonId} side="new" pages={render.new.pages}
          selectedId={selectedId} onSelect={onSelect} className="border-l border-border" />
      </div>
      {render.truncated_pages > 0 && (
        <div className="border-t border-border bg-warning/10 px-4 py-2 text-[11px] text-warning">
          {render.truncated_pages} page(s) not shown (render cap reached).
        </div>
      )}
    </div>
  );
}

function SideColumn({
  comparisonId, side, pages, selectedId, onSelect, className,
}: {
  comparisonId: string; side: "old" | "new"; pages: RenderPage[];
  selectedId?: string | null; onSelect?: (id: string) => void; className?: string;
}) {
  const boxColor = side === "old" ? "bg-sev-critical/25 ring-sev-critical/50" : "bg-success/25 ring-success/50";
  return (
    <div className={cn("space-y-3 p-3", className)}>
      {pages.map((p) => (
        <div key={p.n} className="relative w-full" style={{ aspectRatio: `${p.w_pt} / ${p.h_pt}` }}>
          <img
            src={comparisonPageImageUrl(comparisonId, side, p.n)}
            alt={`${side} page ${p.n}`}
            loading="lazy"
            className="block w-full border border-border"
          />
          {p.boxes.map((b, i) => (
            <button
              key={i}
              id={pixelBoxDomId(b.change_id, side)}
              type="button"
              onClick={onSelect ? () => onSelect(b.change_id) : undefined}
              className={cn(
                "absolute rounded-[1px] ring-1 transition-shadow",
                boxColor,
                selectedId === b.change_id && "ring-2 ring-primary"
              )}
              style={{
                left: `${(b.x0 / p.w_pt) * 100}%`,
                top: `${(b.y0 / p.h_pt) * 100}%`,
                width: `${((b.x1 - b.x0) / p.w_pt) * 100}%`,
                height: `${((b.y1 - b.y0) / p.h_pt) * 100}%`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit checkpoint** (stage only)

```bash
git add frontend/components/compare/PixelDiffViewer.tsx
# STOP for user commit.
```

---

## Task 11: CompareWorkspace — toggle + client polling; detail page passes full comparison

**Files:**
- Modify: `frontend/components/compare/CompareWorkspace.tsx`
- Modify: `frontend/app/(workspace)/compare/[id]/page.tsx`

**Interfaces:**
- Consumes: `PixelDiffViewer` (Task 10), `getComparison` (existing), `RenderResult`.
- Produces: `<CompareWorkspace comparison={DocumentComparison} />` (signature change from `blocks` → full `comparison`).

- [ ] **Step 1: Rewrite `CompareWorkspace.tsx`**
```tsx
"use client";
import * as React from "react";
import { DiffViewer } from "./DiffViewer";
import { PixelDiffViewer } from "./PixelDiffViewer";
import { ChangesPane } from "./ChangesPane";
import { countDiffStats, deriveChanges } from "@/lib/format";
import { getComparison } from "@/lib/api";
import type { DocumentComparison, ChangeItem } from "@/lib/types";

type ViewMode = "pixel" | "text";

export function CompareWorkspace({ comparison }: { comparison: DocumentComparison }) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [live, setLive] = React.useState<DocumentComparison>(comparison);

  // Poll while the pixel render is still processing.
  React.useEffect(() => {
    if (live.render_status !== "processing") return;
    let active = true;
    const t = setInterval(async () => {
      try {
        const fresh = await getComparison(live.id);
        if (!active) return;
        setLive(fresh);
        if (fresh.render_status !== "processing") clearInterval(t);
      } catch { /* keep polling */ }
    }, 2000);
    return () => { active = false; clearInterval(t); };
  }, [live.id, live.render_status]);

  const hasPixel = live.render_status === "completed" && !!live.render_result;
  const [mode, setMode] = React.useState<ViewMode>("pixel");
  const effectiveMode: ViewMode = hasPixel && mode === "pixel" ? "pixel" : "text";

  const blocks = live.diff_result ?? [];
  const changes: ChangeItem[] = React.useMemo(() => {
    if (effectiveMode === "pixel" && live.render_result) {
      return live.render_result.changes.map((c) => ({
        id: c.id, blockIndex: 0, kind: c.kind,
        removedText: c.old?.text, addedText: c.new?.text,
      }));
    }
    return deriveChanges(blocks);
  }, [effectiveMode, live.render_result, blocks]);
  const { removed, added } = React.useMemo(() => countDiffStats(blocks), [blocks]);

  return (
    <div className="space-y-3">
      <ViewToggle
        mode={effectiveMode}
        pixelAvailable={hasPixel}
        pixelPending={live.render_status === "processing"}
        pixelFailed={live.render_status === "failed"}
        onChange={setMode}
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
        {effectiveMode === "pixel" && live.render_result ? (
          <PixelDiffViewer comparisonId={live.id} render={live.render_result}
            selectedId={selectedId} onSelect={setSelectedId} />
        ) : (
          <DiffViewer blocks={blocks} selectedId={selectedId} onSelect={setSelectedId} />
        )}
        <ChangesPane changes={changes} removed={removed} added={added}
          selectedId={selectedId} onSelect={setSelectedId} />
      </div>
    </div>
  );
}

function ViewToggle({
  mode, pixelAvailable, pixelPending, pixelFailed, onChange,
}: {
  mode: ViewMode; pixelAvailable: boolean; pixelPending: boolean;
  pixelFailed: boolean; onChange: (m: ViewMode) => void;
}) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <button type="button" disabled={!pixelAvailable} onClick={() => onChange("pixel")}
        className={`rounded-sm border px-2 py-0.5 ${mode === "pixel" ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground"} ${!pixelAvailable ? "opacity-40" : ""}`}>
        Document view
      </button>
      <button type="button" onClick={() => onChange("text")}
        className={`rounded-sm border px-2 py-0.5 ${mode === "text" ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground"}`}>
        Text view
      </button>
      {pixelPending && <span className="text-muted-foreground">Rendering document view…</span>}
      {pixelFailed && <span className="text-sev-critical">Document view unavailable — showing text.</span>}
    </div>
  );
}
```

- [ ] **Step 2: Update the detail page** `frontend/app/(workspace)/compare/[id]/page.tsx`

Replace the `<CompareWorkspace blocks={comparison.diff_result ?? []} />` line with:
```tsx
        <CompareWorkspace comparison={comparison} />
```
(The page already fetches the full `comparison`; no other change.)

- [ ] **Step 3: Type-check + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: compiles; no type errors.

- [ ] **Step 4: Commit checkpoint** (stage only)

```bash
git add frontend/components/compare/CompareWorkspace.tsx "frontend/app/(workspace)/compare/[id]/page.tsx"
# STOP for user commit.
```

---

## Task 12: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Bring the stack up**

```bash
docker compose up -d gotenberg postgres
docker compose build backend frontend && docker compose up -d backend frontend
docker compose logs backend | grep -i "alembic\|0014" | tail
```
Expected: migration `0014` applied on backend start.

- [ ] **Step 2: Drive the real pair through the UI** (use the superpowers `run`/`verify` approach)

Upload `eTouch II - Ann H - Policy Document 20260708.docx` (original) and `eTouch II_PD_V08.pdf` (revised) via `/compare/new`. On the detail page confirm:
- Document view is the default; page images render in two columns.
- Red boxes appear on the original where content was removed/changed; green boxes on the revised for additions/changes.
- The Changes sidebar count matches; clicking a change scrolls the document view to its box.
- Toggle to Text view still shows the token diff.

- [ ] **Step 3: Failure-path check**

Stop Gotenberg (`docker compose stop gotenberg`), create a docx↔pdf comparison, confirm `render_status` becomes `failed` and the UI falls back to Text view with the note. Restart Gotenberg.

- [ ] **Step 4: Final full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: green (no regressions).

- [ ] **Step 5: Commit checkpoint** (stage only)

```bash
git add -A
# STOP: tell the user the feature is complete and staged for their review/commit.
```

---

## Notes for the implementer

- **`base()` in `api.ts`** differs server- vs browser-side; `comparisonPageImageUrl` and `getComparison` are called from a client component (`CompareWorkspace`), so they resolve to the browser base — correct for `<img>` and polling.
- **Threadpool safety:** Starlette runs sync `BackgroundTasks` functions in a threadpool, so `render_comparison` (sync pdfplumber/pypdfium2/httpx) does not block the event loop.
- **Fail-closed:** any render error sets `render_status=failed` and leaves the text view fully functional — never surface a 500 to the user for a render problem.
- **Do not** hit real Gotenberg or render real pages inside unit tests (Global Constraints). The one real-render check (Task 6 Step 5) and the live smokes (Tasks 8, 12) are manual.
