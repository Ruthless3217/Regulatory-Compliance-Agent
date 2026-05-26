# Precedent-Driven Compliance Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest ~2,600 real reviewer-decision JSON files into a new pgvector index and use them for few-shot "reviewer imitation" compliance analysis, replacing rule retrieval on the analysis path while leaving the existing RAG/rules subsystem fully operational.

**Architecture:** Add one new RAG index (`rag_compliance_examples`) inside the existing `services/rag/` abstraction (same embedders, `PgVectorStore`, migration style, factory). A keyword-based ingestion pipeline parses each `_rl` JSON into `(draft chunk → reviewer comment → anchor → final rewrite → reviewer name → severity)` precedents. `dispatch_node` retrieves per-chunk precedents into `state.retrieved_examples`; `analysis_node` grades each chunk per-chunk against its precedents (few-shot, temperature 0) producing the **unchanged** `ComplianceAnalysisResult` so scoring/frontend/DB persistence are unaffected. A new `/knowledge-base` router adds ingest/stats/projection endpoints plus an additive vector-space visualization page.

**Tech Stack:** FastAPI · SQLAlchemy · Alembic · Postgres + pgvector (IVFFlat) · LangGraph · OpenAI-compatible LLM (Gemini) · `rapidfuzz` · `langchain-text-splitters` · `umap-learn`/`scikit-learn` · Next.js 15 / React 19 / recharts · pytest.

---

## Deviations & Discoveries (read before starting)

These were confirmed by reading the real corpus and code during planning. They refine — not contradict — the spec (`docs/superpowers/specs/2026-05-25-precedent-compliance-engine-design.md`).

1. **Target corpus = `dataset_2.1_rl/`.** The dataset ships two parallel variants:
   - `dataset/Dataset/Dataset/dataset_2.1/` (2,645 files) — raw reviewer data (`word_text`, `full_pdf_text`, structured `word_comments[]`, `changes[]`).
   - `dataset/Dataset/Dataset/dataset_2.1_rl/` (2,617 files) — the instruction-tuning transform whose schema is **exactly** what spec §7 assumes: top-level `task`/`instruction`, `input.draft_text`, `input.compliance_comments` (a newline-joined string of `[Reviewer]: comment (Context: anchor…)`), `output.final_text`, `metadata.document_id`, `metadata.title`.
   The spec's regex parser matched **100% of 8,615 comment lines** across `_rl` with zero failures, and the reviewer-name severity heuristic split cleanly (legal/compliance ≈1,181, marketing ≈1,677, other ≈5,757). **This plan ingests `_rl`.** (If you instead want the richer raw set, that is a different parser and is out of scope here.)
2. **Anchors are truncated** (≈80 chars + a trailing `…`/`...`). Exact substring match will usually miss; the `rapidfuzz.partial_ratio` prefix match is the primary alignment path. The parser strips a trailing `…`/`...` before matching.
3. **`llm_service.generate_structured_response` hardcodes `temperature=0.2`.** We add an additive `temperature` param (default `0.2`, preserving all existing callers) and pass `0.0` from the precedent path.
4. **Frontend has no unit-test runner** (only `npm run typecheck` + `npm run build`). Task 13 verifies via those, not tests.
5. **Unit tests stay synchronous** to match the existing `backend/tests/` style (`sys.path.insert` + plain `def test_*`). No `pytest-asyncio` is introduced; async store/retriever paths are covered by the live smoke test (Task 15).
6. **Docker:** the dataset is not currently mounted into the backend container. Task 4 adds an **additive, read-only** mount `./dataset:/app/dataset:ro` (mirroring the existing `./docs:/app/docs:ro`) so `docker exec … ingest` works; the CLI also resolves a host-relative path so it can run on the host against the exposed Postgres (`localhost:5432`). No existing volume is altered.

---

## File Structure

**New backend files**
- `backend/alembic/versions/0004_compliance_examples.py` — migration: `rag_compliance_examples` table, indexes, tsvector trigger.
- `backend/app/services/rag/indexers/compliance_examples_indexer.py` — embed + batch-upsert precedent rows.
- `backend/app/services/knowledge_base_ingestion.py` — parse / chunk / align / classify / orchestrate ingestion; `get_stats()`.
- `backend/app/services/rag/retrievers/precedent_retriever.py` — per-chunk hybrid precedent lookup + singleton accessor.
- `backend/app/services/agents/validators.py` — `validate_agent_output`.
- `backend/app/services/vector_projection.py` — UMAP/PCA 2-D projection with caching.
- `backend/app/api/routes/knowledge_base.py` — `/knowledge-base/ingest`, `/stats`, `/projection`.
- `backend/scripts/ingest_knowledge_base.py` — ingestion CLI.
- `backend/scripts/eval_precedent_replay.py` — leakage-safe replay eval + metric helpers.
- Tests under `backend/tests/…` (per task).

**Modified backend files**
- `backend/app/services/rag/ports.py` — extend `IndexName`.
- `backend/app/services/rag/stores/pgvector_store.py` — new index in upsert SQL, params, filter whitelist, return columns.
- `backend/app/services/agents/graph/state.py` — add `retrieved_examples`.
- `backend/app/services/agents/graph/nodes.py` — `dispatch_node` precedent retrieval + KB-empty guard; `analysis_node` per-chunk restructure.
- `backend/app/services/preprocessing_service.py` — add `create_precedent_prompts`.
- `backend/app/services/agents/compliance/scoring.py` — extend `SEVERITY_WEIGHTS`.
- `backend/app/services/llm_service.py` — additive `temperature` param.
- `backend/app/config.py` + `backend/.env.example` — new keys.
- `backend/requirements.txt` — new deps.
- `backend/app/main.py` — register router.
- `docker-compose.yml` — additive ro dataset mount.
- `README.md` — Vector Memory System section.

**New / modified frontend files**
- `frontend/app/(workspace)/knowledge-base/page.tsx` — new route.
- `frontend/components/knowledge-base/VectorSpaceScatter.tsx` — recharts scatter.
- `frontend/lib/api.ts` — `getKnowledgeBaseProjection()`.
- `frontend/lib/types.ts` — `ProjectionPoint`, `ProjectionResponse`.
- `frontend/components/workspace/Sidebar.tsx` — one nav entry.

---

## Conventions for every task

- Backend test command runs from `backend/`: `python -m pytest <path> -v`.
- Commit after each task's tests/verification pass. Branch is feature work; do not push unless asked.
- Embedder/store are reached only via `app.services.rag.factory.get_embedder()` / `get_vector_store()`.
- New severity vocab is `critical | moderate | informational`; new categories are `terminology issue | legal language | missing reference | disclaimer issue | other`.

---

### Task 1: Migration `0004` + `IndexName` + pgvector_store wiring

**Files:**
- Create: `backend/alembic/versions/0004_compliance_examples.py`
- Modify: `backend/app/services/rag/ports.py:14`
- Modify: `backend/app/services/rag/stores/pgvector_store.py` (`_FILTER_WHITELIST`, `_RETURN_COLUMNS`, `_UPSERT_SQL`, `_upsert_params`)
- Test: `backend/tests/services/rag/stores/test_compliance_examples_upsert.py`

- [ ] **Step 1: Write the failing unit test for the new-index upsert params**

Create `backend/tests/services/rag/stores/test_compliance_examples_upsert.py`:

```python
"""Pure-unit tests for PgVectorStore rag_compliance_examples upsert wiring."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.rag.ports import IndexName, VectorDoc
from app.services.rag.stores import pgvector_store as pg


def test_index_name_includes_compliance_examples():
    # Literal membership check via the store's dispatch dicts.
    assert "rag_compliance_examples" in pg._UPSERT_SQL
    assert "rag_compliance_examples" in pg._RETURN_COLUMNS
    assert "rag_compliance_examples" in pg._FILTER_WHITELIST


def test_filter_whitelist_fields():
    assert pg._FILTER_WHITELIST["rag_compliance_examples"] == {
        "reviewer_name",
        "violation_category",
        "severity",
        "document_id",
    }


def test_upsert_params_maps_all_columns():
    doc = VectorDoc(
        id="11111111-1111-1111-1111-111111111111",
        embedding=[0.1, 0.2, 0.3],
        fields={
            "document_id": "10699",
            "title": "Untitled",
            "task": "insurance_compliance_rewrite",
            "section_label": None,
            "chunk_text": "draft chunk text",
            "anchor_text": "anchor",
            "reviewer_name": "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life",
            "comment_text": "Add product disclaimer below.",
            "final_text_chunk": "final rewrite",
            "violation_category": "disclaimer issue",
            "severity": "critical",
            "source_file": "10699_request_10699.json",
            "embed_text": "Document chunk: draft chunk text\nCompliance comment: Add product disclaimer below.",
        },
    )
    p = pg._upsert_params("rag_compliance_examples", doc)
    assert p["id"] == doc.id
    assert p["document_id"] == "10699"
    assert p["reviewer_name"].endswith("/Life")
    assert p["violation_category"] == "disclaimer issue"
    assert p["severity"] == "critical"
    assert p["source_file"] == "10699_request_10699.json"
    # embedding serialized to pgvector literal
    assert p["embedding"].startswith("[") and p["embedding"].endswith("]")


def test_upsert_params_defaults_missing_optionals():
    doc = VectorDoc(
        id="22222222-2222-2222-2222-222222222222",
        embedding=[0.0],
        fields={
            "document_id": "1",
            "chunk_text": "c",
            "comment_text": "m",
            "source_file": "f.json",
            "embed_text": "e",
        },
    )
    p = pg._upsert_params("rag_compliance_examples", doc)
    assert p["title"] is None
    assert p["anchor_text"] is None
    assert p["final_text_chunk"] is None
    assert p["violation_category"] == "other"
    assert p["severity"] == "informational"
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/rag/stores/test_compliance_examples_upsert.py -v`
Expected: FAIL — `KeyError: 'rag_compliance_examples'` (dispatch dicts don't have it yet).

- [ ] **Step 3: Extend the `IndexName` Literal**

In `backend/app/services/rag/ports.py`, change line 14:

```python
IndexName = Literal["rag_rules", "rag_chunks", "rag_source_docs", "rag_compliance_examples"]
```

- [ ] **Step 4: Wire the new index into `pgvector_store.py`**

In `backend/app/services/rag/stores/pgvector_store.py`, add to `_FILTER_WHITELIST` (after the `rag_source_docs` entry):

```python
    "rag_compliance_examples": {
        "reviewer_name", "violation_category", "severity", "document_id",
    },
```

Add to `_RETURN_COLUMNS`:

```python
    "rag_compliance_examples": [
        "id", "document_id", "title", "task", "section_label", "chunk_text",
        "anchor_text", "reviewer_name", "comment_text", "final_text_chunk",
        "violation_category", "severity", "source_file",
    ],
```

Add to `_UPSERT_SQL`:

```python
    "rag_compliance_examples": """
        INSERT INTO rag_compliance_examples (
            id, document_id, title, task, section_label, chunk_text, anchor_text,
            reviewer_name, comment_text, final_text_chunk, violation_category,
            severity, source_file, embed_text, embedding)
        VALUES (
            :id, :document_id, :title, :task, :section_label, :chunk_text, :anchor_text,
            :reviewer_name, :comment_text, :final_text_chunk, :violation_category,
            :severity, :source_file, :embed_text, CAST(:embedding AS VECTOR))
        ON CONFLICT (id) DO UPDATE SET
          document_id = EXCLUDED.document_id,
          title = EXCLUDED.title,
          task = EXCLUDED.task,
          section_label = EXCLUDED.section_label,
          chunk_text = EXCLUDED.chunk_text,
          anchor_text = EXCLUDED.anchor_text,
          reviewer_name = EXCLUDED.reviewer_name,
          comment_text = EXCLUDED.comment_text,
          final_text_chunk = EXCLUDED.final_text_chunk,
          violation_category = EXCLUDED.violation_category,
          severity = EXCLUDED.severity,
          source_file = EXCLUDED.source_file,
          embed_text = EXCLUDED.embed_text,
          embedding = EXCLUDED.embedding
    """,
```

In `_upsert_params`, add this branch before the final `raise ValueError`:

```python
    if index == "rag_compliance_examples":
        return {
            **base,
            "document_id": f.get("document_id", ""),
            "title": f.get("title"),
            "task": f.get("task"),
            "section_label": f.get("section_label"),
            "chunk_text": f.get("chunk_text", ""),
            "anchor_text": f.get("anchor_text"),
            "reviewer_name": f.get("reviewer_name"),
            "comment_text": f.get("comment_text", ""),
            "final_text_chunk": f.get("final_text_chunk"),
            "violation_category": f.get("violation_category", "other"),
            "severity": f.get("severity", "informational"),
            "source_file": f.get("source_file", ""),
            "embed_text": f.get("embed_text", ""),
        }
```

- [ ] **Step 5: Run the test; expect pass**

Run: `python -m pytest tests/services/rag/stores/test_compliance_examples_upsert.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Author the migration**

Create `backend/alembic/versions/0004_compliance_examples.py`:

```python
"""rag_compliance_examples — precedent reviewer-decision vectors (pgvector + tsvector).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25
"""
import os
from typing import Sequence, Union
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same env-driven dim as the existing three indexes (openai=1536, cohere=1024).
EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        f"""
        CREATE TABLE rag_compliance_examples (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id TEXT NOT NULL,
            title TEXT,
            task TEXT,
            section_label TEXT,
            chunk_text TEXT NOT NULL,
            anchor_text TEXT,
            reviewer_name TEXT,
            comment_text TEXT NOT NULL,
            final_text_chunk TEXT,
            violation_category TEXT,
            severity TEXT,
            source_file TEXT NOT NULL,
            embed_text TEXT NOT NULL,
            embedding VECTOR({EMBED_DIM}) NOT NULL,
            search_tsv TSVECTOR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.create_index("ix_rag_ce_reviewer", "rag_compliance_examples", ["reviewer_name"])
    op.create_index("ix_rag_ce_category", "rag_compliance_examples", ["violation_category"])
    op.create_index("ix_rag_ce_severity", "rag_compliance_examples", ["severity"])
    op.create_index("ix_rag_ce_document_id", "rag_compliance_examples", ["document_id"])
    op.create_index("ix_rag_ce_source_file", "rag_compliance_examples", ["source_file"])
    op.execute(
        "CREATE INDEX ix_rag_ce_tsv ON rag_compliance_examples USING GIN (search_tsv)"
    )
    op.execute(
        "CREATE INDEX ix_rag_ce_embedding ON rag_compliance_examples "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION rag_compliance_examples_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', COALESCE(NEW.chunk_text, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.comment_text, '')), 'B');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER rag_compliance_examples_tsv_trg "
        "BEFORE INSERT OR UPDATE ON rag_compliance_examples "
        "FOR EACH ROW EXECUTE FUNCTION rag_compliance_examples_tsv_trigger()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS rag_compliance_examples_tsv_trg ON rag_compliance_examples"
    )
    op.execute("DROP FUNCTION IF EXISTS rag_compliance_examples_tsv_trigger")
    op.drop_table("rag_compliance_examples")
    # Don't drop the vector extension — other tables use it.
```

- [ ] **Step 7: Verify the migration applies (needs Docker Postgres up)**

Run: `docker compose up -d postgres && docker exec compliance-backend alembic upgrade head`
Expected: ends at revision `0004`; `docker exec compliance-postgres psql -U compliance_user -d compliance_db -c "\d rag_compliance_examples"` lists the columns + 7 indexes.
(If running on host instead: `cd backend && alembic upgrade head`.)

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0004_compliance_examples.py backend/app/services/rag/ports.py backend/app/services/rag/stores/pgvector_store.py backend/tests/services/rag/stores/test_compliance_examples_upsert.py
git commit -m "feat(rag): add rag_compliance_examples index, migration 0004, store wiring"
```

---

### Task 2: Dependencies + config + env keys

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py:60-71` (RAG section)
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config_kb_keys.py`

- [ ] **Step 1: Write the failing config test**

Create `backend/tests/test_config_kb_keys.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


def test_kb_defaults_present():
    assert settings.pgvector_top_k == 5
    assert settings.kb_chunk_size == 500
    assert settings.kb_chunk_overlap == 50
    assert settings.kb_batch_size == 100
    assert settings.kb_min_fuzzy_score == 60
    assert settings.viz_points_per_index == 2000
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/test_config_kb_keys.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'pgvector_top_k'`.

- [ ] **Step 3: Add settings**

In `backend/app/config.py`, after the `rag_active_categories` line (≈line 71), add:

```python

    # Precedent compliance engine (Phase 1)
    pgvector_top_k: int = 5                       # precedents retrieved per chunk
    kb_chunk_size: int = 500
    kb_chunk_overlap: int = 50
    kb_batch_size: int = 100
    kb_min_fuzzy_score: int = 60                  # rapidfuzz partial_ratio threshold
    viz_points_per_index: int = 2000              # projection point cap per index
```

- [ ] **Step 4: Add dependencies to `requirements.txt`**

Append to `backend/requirements.txt`:

```
# Precedent compliance engine (Phase 1)
rapidfuzz>=3.10.0
langchain-text-splitters>=0.3.0
umap-learn>=0.5.6
scikit-learn>=1.5.0
```

- [ ] **Step 5: Add env documentation**

Append to `backend/.env.example`:

```
# Precedent compliance engine (Phase 1)
PGVECTOR_TOP_K=5
KB_CHUNK_SIZE=500
KB_CHUNK_OVERLAP=50
KB_BATCH_SIZE=100
KB_MIN_FUZZY_SCORE=60
VIZ_POINTS_PER_INDEX=2000
```

- [ ] **Step 6: Install + run the test**

Run: `cd backend && pip install -r requirements.txt && python -m pytest tests/test_config_kb_keys.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/app/config.py backend/.env.example backend/tests/test_config_kb_keys.py
git commit -m "feat(config): add precedent-engine settings + deps (rapidfuzz, text-splitters, umap, sklearn)"
```

---

### Task 3: Ingestion pipeline — parser, classifier, alignment, indexer, service

**Files:**
- Create: `backend/app/services/rag/indexers/compliance_examples_indexer.py`
- Create: `backend/app/services/knowledge_base_ingestion.py`
- Test: `backend/tests/services/test_kb_ingestion_parsing.py`

The pure-logic functions (parse, classify, align, chunk-pair) are unit-tested. The embed/upsert path and `get_stats()` are exercised in the Task 15 smoke test.

- [ ] **Step 1: Write failing unit tests for parsing/classification/alignment**

Create `backend/tests/services/test_kb_ingestion_parsing.py`:

```python
"""Pure-unit tests for knowledge-base ingestion parsing/classification/alignment."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.knowledge_base_ingestion import (
    parse_compliance_comments,
    classify_category,
    classify_severity,
    align_comment_to_chunk,
    ParsedComment,
)


def test_parse_single_comment_line():
    raw = "[Shailja Saklani/Pune HO/Legal Compliance and FPU/Life]: Add product disclaimer below. (Context: For instance, Bajaj Allianz Smart Protection Goal is a comprehensive term insura...)"
    out = parse_compliance_comments(raw, source_file="x.json")
    assert len(out) == 1
    c = out[0]
    assert c.reviewer == "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life"
    assert c.comment == "Add product disclaimer below."
    assert c.anchor.startswith("For instance, Bajaj Allianz Smart Protection Goal")
    # trailing ellipsis stripped
    assert not c.anchor.endswith("...")
    assert not c.anchor.endswith("…")


def test_parse_multiple_lines_and_blank_lines():
    raw = (
        "[Rituraj Singh/Pune HO/Marketing/Life]: Logic is incorrect (Context: The plan runs for...)\n"
        "\n"
        "[Sahana Rao/Bangalore/Legal Compliance and FPU/Life]: Add tax disclaimer (Context: Disclaimers:)"
    )
    out = parse_compliance_comments(raw, source_file="x.json")
    assert len(out) == 2
    assert out[1].comment == "Add tax disclaimer"
    assert out[1].anchor == "Disclaimers:"


def test_parse_empty_string_returns_empty():
    assert parse_compliance_comments("", source_file="x.json") == []
    assert parse_compliance_comments(None, source_file="x.json") == []


def test_classify_category_keywords():
    assert classify_category("Please rephrase this word") == "terminology issue"
    assert classify_category("This is not IRDAI compliant") == "legal language"
    assert classify_category("Add a source link / reference") == "missing reference"
    assert classify_category("Add the disclaimer here") == "disclaimer issue"
    assert classify_category("Not clear") == "other"


def test_classify_severity_from_reviewer():
    assert classify_severity("Shailja Saklani/Pune HO/Legal Compliance and FPU/Life") == "critical"
    assert classify_severity("Rituraj Singh/Pune HO/Marketing/Life") == "moderate"
    assert classify_severity("Microsoft Office User") == "informational"


def test_align_exact_substring_first():
    chunks = ["intro chunk text", "For instance, Bajaj Allianz Smart Protection Goal is great"]
    idx, score = align_comment_to_chunk("For instance, Bajaj Allianz Smart Protection Goal", chunks, min_fuzzy=60)
    assert idx == 1
    assert score == 100


def test_align_fuzzy_on_truncated_anchor():
    chunks = ["unrelated", "Assess whether the sum assured is suitable for your financial needs. You can use a calculator."]
    # truncated anchor (prefix only)
    idx, score = align_comment_to_chunk("Assess whether the sum assured is suitable for your financial needs. You can use", chunks, min_fuzzy=60)
    assert idx == 1
    assert score >= 60


def test_align_returns_none_below_threshold():
    chunks = ["completely different content about taxation"]
    idx, score = align_comment_to_chunk("zzz qqq never appears", chunks, min_fuzzy=60)
    assert idx is None
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/test_kb_ingestion_parsing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.knowledge_base_ingestion'`.

- [ ] **Step 3: Write the indexer**

Create `backend/app/services/rag/indexers/compliance_examples_indexer.py`:

```python
"""Compliance-examples indexer — embeds precedent rows and batch-upserts them
into rag_compliance_examples. Mirrors rules_indexer's embed→VectorDoc→store path.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


def build_embed_text(chunk_text: str, comment_text: str) -> str:
    return f"Document chunk: {chunk_text}\nCompliance comment: {comment_text}"


def _row_to_doc(row: Dict[str, Any], embedding: List[float]) -> VectorDoc:
    return VectorDoc(
        id=row["id"],
        embedding=embedding,
        fields={
            "document_id": row.get("document_id", ""),
            "title": row.get("title"),
            "task": row.get("task"),
            "section_label": row.get("section_label"),
            "chunk_text": row.get("chunk_text", ""),
            "anchor_text": row.get("anchor_text"),
            "reviewer_name": row.get("reviewer_name"),
            "comment_text": row.get("comment_text", ""),
            "final_text_chunk": row.get("final_text_chunk"),
            "violation_category": row.get("violation_category", "other"),
            "severity": row.get("severity", "informational"),
            "source_file": row.get("source_file", ""),
            "embed_text": build_embed_text(row.get("chunk_text", ""), row.get("comment_text", "")),
        },
    )


async def upsert_examples(rows: Iterable[Dict[str, Any]]) -> int:
    """Embed `embed_text` for each row and upsert. Returns number indexed."""
    rows = list(rows)
    if not rows:
        return 0
    embedder = get_embedder()
    store = get_vector_store()
    try:
        texts = [build_embed_text(r.get("chunk_text", ""), r.get("comment_text", "")) for r in rows]
        vectors = await embedder.embed(texts)
        docs = [_row_to_doc(r, v) for r, v in zip(rows, vectors)]
        await store.upsert("rag_compliance_examples", docs)
        logger.info(f"Indexed {len(docs)} precedents into rag_compliance_examples")
        return len(docs)
    except RAGDegraded as e:
        raise RAGIndexingFailed(str(e)) from e
```

- [ ] **Step 4: Write the ingestion service**

Create `backend/app/services/knowledge_base_ingestion.py`:

```python
"""Knowledge-base ingestion — parse _rl JSON reviewer decisions into precedent
rows and index them into rag_compliance_examples.

Corpus schema (dataset_2.1_rl/*.json):
    task, instruction,
    input.draft_text, input.compliance_comments (str),
    output.final_text,
    metadata.document_id, metadata.title

compliance_comments lines look like:
    [Reviewer Name/Org]: the comment text (Context: anchor text…)
Anchors are truncated (~80 chars + ellipsis); alignment uses fuzzy prefix match.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Deterministic namespace so re-ingesting the same (source_file, chunk, comment)
# produces the same id → ON CONFLICT (id) makes ingestion idempotent.
_NS = uuid.UUID("6f1c0b2e-0000-4000-8000-000000000004")

_COMMENT_RE = re.compile(
    r"^\[(?P<rev>.+?)\]:\s*(?P<comment>.*?)\s*\(Context:\s*(?P<anchor>.*?)\)\s*$"
)

_CATEGORY_KEYWORDS = [
    ("terminology issue", ("terminology", "rephrase", "word", "rename", "phrase")),
    ("legal language", ("legal", "legally", "irdai", "regulatory", "regulation", "compliance")),
    ("missing reference", ("link", "url", "reference", "refer", "source", "cite")),
    ("disclaimer issue", ("disclaimer", "disclosure", "disclaim")),
]

PARSE_ERROR_LOG = os.path.join("logs", "parse_errors.log")


@dataclass
class ParsedComment:
    reviewer: str
    comment: str
    anchor: str


def _strip_ellipsis(s: str) -> str:
    s = s.strip()
    for suffix in ("…", "..."):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _log_parse_error(source_file: str, line_no: int, line: str) -> None:
    os.makedirs("logs", exist_ok=True)
    with open(PARSE_ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"{source_file}\tline {line_no}\t{line}\n")


def parse_compliance_comments(raw: Optional[str], source_file: str) -> List[ParsedComment]:
    """Parse the compliance_comments string into structured comments.
    Malformed lines are logged (never silently dropped)."""
    if not raw or not raw.strip():
        return []
    out: List[ParsedComment] = []
    for i, line in enumerate(raw.split("\n"), start=1):
        line = line.strip()
        if not line:
            continue
        m = _COMMENT_RE.match(line)
        if not m:
            _log_parse_error(source_file, i, line)
            continue
        out.append(
            ParsedComment(
                reviewer=m.group("rev").strip(),
                comment=m.group("comment").strip(),
                anchor=_strip_ellipsis(m.group("anchor")),
            )
        )
    return out


def classify_category(comment: str) -> str:
    c = (comment or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(k in c for k in keywords):
            return category
    return "other"


def classify_severity(reviewer_name: str) -> str:
    r = reviewer_name or ""
    if re.search(r"Legal|Compliance", r):
        return "critical"
    if re.search(r"Marketing", r):
        return "moderate"
    return "informational"


def align_comment_to_chunk(
    anchor: str, chunks: List[str], min_fuzzy: int
) -> Tuple[Optional[int], int]:
    """Return (chunk_index, score). Exact substring → 100; else best fuzzy
    partial_ratio on first 100 chars if >= min_fuzzy; else (None, best)."""
    if not anchor or not chunks:
        return None, 0
    needle = anchor[:100]
    # Exact substring first.
    for idx, ch in enumerate(chunks):
        if anchor and anchor in ch:
            return idx, 100
    # Fuzzy.
    best_idx, best_score = None, 0
    for idx, ch in enumerate(chunks):
        score = int(fuzz.partial_ratio(needle, ch[:400]))
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is not None and best_score >= min_fuzzy:
        return best_idx, best_score
    return None, best_score


def _chunk_text(content: str) -> List[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.kb_chunk_size,
        chunk_overlap=settings.kb_chunk_overlap,
    )
    return [c for c in splitter.split_text(content or "") if c.strip()]


def _pair_final_chunk(final_chunks: List[str], draft_idx: int) -> Optional[str]:
    """Best-effort positional pairing of a draft chunk to a final chunk."""
    if not final_chunks:
        return None
    if draft_idx < len(final_chunks):
        return final_chunks[draft_idx]
    return None


class KnowledgeBaseIngestionService:
    """Parses _rl JSON files into precedent rows and indexes them."""

    def parse_file(self, path: str) -> Dict[str, Any]:
        """Parse one file into {rows, unmatched, document_id}. Raises on missing fields."""
        source_file = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        inp = data.get("input") or {}
        out = data.get("output") or {}
        meta = data.get("metadata") or {}
        draft = inp.get("draft_text")
        if not isinstance(draft, str) or not draft.strip():
            raise ValueError(f"{source_file}: missing input.draft_text")

        document_id = str(meta.get("document_id") or "")
        title = meta.get("title")
        task = data.get("task")
        final_text = out.get("final_text") or ""

        draft_chunks = _chunk_text(draft)
        final_chunks = _chunk_text(final_text)
        comments = parse_compliance_comments(inp.get("compliance_comments"), source_file)

        rows: List[Dict[str, Any]] = []
        unmatched = 0
        for pc in comments:
            idx, _score = align_comment_to_chunk(
                pc.anchor, draft_chunks, settings.kb_min_fuzzy_score
            )
            if idx is None:
                unmatched += 1
                logger.debug(f"{source_file}: unmatched comment anchor: {pc.anchor[:60]!r}")
                continue
            chunk_text = draft_chunks[idx]
            comment_text = pc.comment
            key = f"{source_file}|{chunk_text}|{comment_text}"
            rows.append(
                {
                    "id": str(uuid.uuid5(_NS, key)),
                    "document_id": document_id,
                    "title": title,
                    "task": task,
                    "section_label": None,
                    "chunk_text": chunk_text,
                    "anchor_text": pc.anchor,
                    "reviewer_name": pc.reviewer,
                    "comment_text": comment_text,
                    "final_text_chunk": _pair_final_chunk(final_chunks, idx),
                    "violation_category": classify_category(comment_text),
                    "severity": classify_severity(pc.reviewer),
                    "source_file": source_file,
                }
            )
        return {"rows": rows, "unmatched": unmatched, "document_id": document_id}

    def _existing_ids(self, ids: List[str]) -> set:
        if not ids:
            return set()
        db = SessionLocal()
        try:
            res = db.execute(
                text("SELECT id FROM rag_compliance_examples WHERE id = ANY(CAST(:ids AS UUID[]))"),
                {"ids": "{" + ",".join(ids) + "}"},
            ).all()
            return {str(r[0]) for r in res}
        finally:
            db.close()

    async def ingest_folder(
        self, folder: str, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Parse + index every *.json in folder. Returns a summary dict."""
        from app.services.rag.indexers.compliance_examples_indexer import upsert_examples

        files = sorted(
            os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json")
        )
        if limit:
            files = files[:limit]

        started = datetime.utcnow()
        processed = failed = inserted = skipped = unmatched_total = 0
        doc_ids: Dict[str, int] = {}
        batch: List[Dict[str, Any]] = []

        async def flush(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
            if not rows:
                return 0, 0
            existing = self._existing_ids([r["id"] for r in rows])
            fresh = [r for r in rows if r["id"] not in existing]
            n = await upsert_examples(fresh)
            return n, len(rows) - len(fresh)

        for path in files:
            try:
                parsed = self.parse_file(path)
                processed += 1
                unmatched_total += parsed["unmatched"]
                did = parsed["document_id"]
                doc_ids[did] = doc_ids.get(did, 0) + 1
                batch.extend(parsed["rows"])
                if len(batch) >= settings.kb_batch_size:
                    n, dup = await flush(batch)
                    inserted += n
                    skipped += dup
                    batch = []
            except Exception as e:
                failed += 1
                logger.warning(f"Ingest failed for {os.path.basename(path)}: {e}")

        n, dup = await flush(batch)
        inserted += n
        skipped += dup

        duplicate_doc_ids = {d: c for d, c in doc_ids.items() if c > 1}
        return {
            "folder": folder,
            "files_processed": processed,
            "files_failed": failed,
            "inserted": inserted,
            "skipped_duplicates": skipped,
            "unmatched_comments": unmatched_total,
            "distinct_document_ids": len(doc_ids),
            "duplicate_document_id_count": len(duplicate_doc_ids),
            "elapsed_seconds": round((datetime.utcnow() - started).total_seconds(), 2),
        }

    def get_stats(self) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            total = db.execute(text("SELECT COUNT(*) FROM rag_compliance_examples")).scalar() or 0

            def _counts(col: str) -> Dict[str, int]:
                rows = db.execute(
                    text(f"SELECT {col}, COUNT(*) FROM rag_compliance_examples GROUP BY {col}")
                ).all()
                return {str(r[0]): int(r[1]) for r in rows}

            distinct_files = db.execute(
                text("SELECT COUNT(DISTINCT source_file) FROM rag_compliance_examples")
            ).scalar() or 0
            latest = db.execute(
                text("SELECT MAX(created_at) FROM rag_compliance_examples")
            ).scalar()
            return {
                "total": int(total),
                "by_violation_category": _counts("violation_category"),
                "by_severity": _counts("severity"),
                "by_reviewer_name": _counts("reviewer_name"),
                "distinct_source_files": int(distinct_files),
                "most_recent_created_at": latest.isoformat() if latest else None,
            }
        finally:
            db.close()


_singleton: Optional[KnowledgeBaseIngestionService] = None


def get_kb_ingestion_service() -> KnowledgeBaseIngestionService:
    global _singleton
    if _singleton is None:
        _singleton = KnowledgeBaseIngestionService()
    return _singleton
```

- [ ] **Step 5: Run the unit tests; expect pass**

Run: `python -m pytest tests/services/test_kb_ingestion_parsing.py -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/rag/indexers/compliance_examples_indexer.py backend/app/services/knowledge_base_ingestion.py backend/tests/services/test_kb_ingestion_parsing.py
git commit -m "feat(kb): precedent ingestion service (parse/classify/align) + indexer"
```

---

### Task 4: Ingestion CLI + dataset volume mount

**Files:**
- Create: `backend/scripts/ingest_knowledge_base.py`
- Modify: `docker-compose.yml:56-59` (backend volumes — additive)

- [ ] **Step 1: Write the CLI**

Create `backend/scripts/ingest_knowledge_base.py`:

```python
"""Ingest the precedent knowledge base (dataset_2.1_rl JSON) into rag_compliance_examples.

Usage:
  # In the backend container (after the additive ro mount in docker-compose):
  docker exec compliance-backend python -m scripts.ingest_knowledge_base
  docker exec compliance-backend python -m scripts.ingest_knowledge_base --limit 10 --preview

  # On the host (Postgres exposed on localhost:5432; embedder env must be set):
  cd backend && python -m scripts.ingest_knowledge_base --folder ../dataset/Dataset/Dataset/dataset_2.1_rl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.knowledge_base_ingestion import get_kb_ingestion_service  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ingest_knowledge_base")

# Container mount first, then host-relative fallbacks.
DEFAULT_FOLDERS = [
    "/app/dataset/Dataset/Dataset/dataset_2.1_rl",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "dataset", "Dataset", "Dataset", "dataset_2.1_rl")
    ),
]


def _resolve_folder(arg: str | None) -> str:
    if arg:
        return arg
    for cand in DEFAULT_FOLDERS:
        if os.path.isdir(cand):
            return cand
    raise FileNotFoundError(
        "dataset_2.1_rl not found. Pass --folder. Tried: " + ", ".join(DEFAULT_FOLDERS)
    )


def _preview(folder: str) -> None:
    svc = get_kb_ingestion_service()
    files = sorted(f for f in os.listdir(folder) if f.endswith(".json"))
    if not files:
        print(f"No JSON files in {folder}")
        return
    path = os.path.join(folder, files[0])
    parsed = svc.parse_file(path)
    print(f"Preview of {files[0]}:")
    print(f"  document_id={parsed['document_id']}  rows={len(parsed['rows'])}  unmatched={parsed['unmatched']}")
    for r in parsed["rows"][:8]:
        print("  ---")
        print(f"  reviewer : {r['reviewer_name']}")
        print(f"  severity : {r['severity']}  category: {r['violation_category']}")
        print(f"  comment  : {r['comment_text'][:100]}")
        print(f"  chunk    : {r['chunk_text'][:100]}")
    try:
        input("\nPress Enter to exit preview (no data written)...")
    except EOFError:
        pass


async def _run(folder: str, limit: int | None) -> None:
    svc = get_kb_ingestion_service()
    logger.info(f"Ingesting from {folder} (limit={limit})")
    summary = await svc.ingest_folder(folder, limit=limit)
    logger.info("=" * 60)
    logger.info("Ingestion summary:\n" + json.dumps(summary, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest precedent knowledge base")
    p.add_argument("--folder", help="Path to dataset_2.1_rl (auto-resolved if omitted)")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N files")
    p.add_argument("--preview", action="store_true", help="Parse one file, print pairings, exit")
    args = p.parse_args()

    folder = _resolve_folder(args.folder)
    if args.preview:
        _preview(folder)
        return
    asyncio.run(_run(folder, args.limit))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the additive read-only dataset mount**

In `docker-compose.yml`, under `backend:` → `volumes:` (currently lines 56-59), add one line so the list reads:

```yaml
    volumes:
      - ./backend/uploads:/app/uploads
      - ./backend/logs:/app/logs
      - ./docs:/app/docs:ro
      - ./dataset:/app/dataset:ro
```

- [ ] **Step 3: Verify CLI argument parsing + preview without DB writes**

Run (host): `cd backend && python -m scripts.ingest_knowledge_base --folder ../dataset/Dataset/Dataset/dataset_2.1_rl --preview`
Expected: prints a document_id, a positive `rows=` count, and up to 8 reviewer/severity/category/comment/chunk previews; waits for Enter; writes nothing.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/ingest_knowledge_base.py docker-compose.yml
git commit -m "feat(kb): ingestion CLI + additive read-only dataset mount"
```

---

### Task 5: Knowledge-base API router (ingest + stats) and registration

**Files:**
- Create: `backend/app/api/routes/knowledge_base.py`
- Modify: `backend/app/main.py:6` (import) and `:113` (include_router)
- Test: `backend/tests/test_kb_router_smoke.py`

- [ ] **Step 1: Write a failing import/shape test**

Create `backend/tests/test_kb_router_smoke.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import knowledge_base


def test_router_prefix_and_routes():
    paths = {r.path for r in knowledge_base.router.routes}
    assert "/knowledge-base/ingest" in paths
    assert "/knowledge-base/stats" in paths


def test_registered_in_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/knowledge-base/stats" in paths
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/test_kb_router_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: app.api.routes.knowledge_base`.

- [ ] **Step 3: Write the router (ingest + stats only; projection added in Task 12)**

Create `backend/app/api/routes/knowledge_base.py`:

```python
"""Knowledge-base endpoints — ingest precedent corpus + report stats.

Projection endpoint (GET /knowledge-base/projection) is added in a later task.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.knowledge_base_ingestion import get_kb_ingestion_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])


class IngestRequest(BaseModel):
    folder_path: str = Field(..., description="Absolute path to a folder of _rl JSON files")
    preview: bool = False
    limit: Optional[int] = Field(None, ge=1)


@router.post("/ingest")
async def ingest_knowledge_base(req: IngestRequest):
    if not os.path.isdir(req.folder_path):
        raise HTTPException(status_code=400, detail=f"Folder not found: {req.folder_path}")
    svc = get_kb_ingestion_service()
    if req.preview:
        files = sorted(f for f in os.listdir(req.folder_path) if f.endswith(".json"))
        if not files:
            raise HTTPException(status_code=400, detail="No JSON files in folder")
        parsed = svc.parse_file(os.path.join(req.folder_path, files[0]))
        return {
            "preview": True,
            "file": files[0],
            "document_id": parsed["document_id"],
            "row_count": len(parsed["rows"]),
            "unmatched": parsed["unmatched"],
            "rows": parsed["rows"][:8],
        }
    try:
        return await svc.ingest_folder(req.folder_path, limit=req.limit)
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")


@router.get("/stats")
async def knowledge_base_stats():
    svc = get_kb_ingestion_service()
    try:
        return svc.get_stats()
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        raise HTTPException(status_code=500, detail=f"Stats failed: {e}")
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/app/main.py`, line 6, add `knowledge_base` to the import:

```python
from .api.routes import submissions, compliance, dashboard, rules, chat, similar, rag_health, knowledge_base
```

After line 113 (`app.include_router(rag_health.router)`), add:

```python
app.include_router(knowledge_base.router)
```

- [ ] **Step 5: Run the test; expect pass**

Run: `python -m pytest tests/test_kb_router_smoke.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/knowledge_base.py backend/app/main.py backend/tests/test_kb_router_smoke.py
git commit -m "feat(api): knowledge-base ingest + stats endpoints"
```

---

### Task 6: PrecedentRetriever + `ComplianceState.retrieved_examples`

**Files:**
- Create: `backend/app/services/rag/retrievers/precedent_retriever.py`
- Modify: `backend/app/services/agents/graph/state.py:23` (add field)
- Test: `backend/tests/services/rag/retrievers/test_precedent_retriever_shape.py`

- [ ] **Step 1: Write a failing test for the retriever's result-shaping helper**

The live search needs a DB; we unit-test the pure hit→dict shaping helper.

Create `backend/tests/services/rag/retrievers/test_precedent_retriever_shape.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from app.services.rag.ports import SearchHit
from app.services.rag.retrievers.precedent_retriever import _hit_to_precedent


def test_hit_to_precedent_maps_fields_and_score():
    hit = SearchHit(
        id="abc",
        score=0.73,
        fields={
            "reviewer_name": "Ayushi Sharma/Pune HO/Legal Compliance and FPU/Life",
            "comment_text": "Add disclaimer",
            "chunk_text": "draft chunk",
            "final_text_chunk": "final",
            "violation_category": "disclaimer issue",
            "severity": "critical",
            "document_id": "123",
            "anchor_text": "anchor",
        },
    )
    p = _hit_to_precedent(hit)
    assert p["id"] == "abc"
    assert p["score"] == 0.73
    assert p["reviewer_name"].endswith("/Life")
    assert p["comment_text"] == "Add disclaimer"
    assert p["chunk_text"] == "draft chunk"
    assert p["final_text_chunk"] == "final"
    assert p["violation_category"] == "disclaimer issue"
    assert p["severity"] == "critical"
    assert p["document_id"] == "123"
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/rag/retrievers/test_precedent_retriever_shape.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the retriever**

Create `backend/app/services/rag/retrievers/precedent_retriever.py`:

```python
"""Precedent retriever — per-chunk hybrid lookup against rag_compliance_examples.

Returns { chunk_id: [precedent_dict, ...] }. Used by dispatch_node to attach
state.retrieved_examples for the precedent analysis path.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit

logger = logging.getLogger(__name__)


def _hit_to_precedent(hit: SearchHit) -> Dict[str, Any]:
    f = hit.fields or {}
    return {
        "id": hit.id,
        "score": hit.score,
        "reviewer_name": f.get("reviewer_name"),
        "comment_text": f.get("comment_text"),
        "chunk_text": f.get("chunk_text"),
        "anchor_text": f.get("anchor_text"),
        "final_text_chunk": f.get("final_text_chunk"),
        "violation_category": f.get("violation_category"),
        "severity": f.get("severity"),
        "document_id": f.get("document_id"),
    }


class PrecedentRetriever:
    async def retrieve_per_chunk(
        self,
        chunks: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        exclude_document_id: Optional[str] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """For each chunk, return its top_k most-similar precedents.
        On embed/store failure for a chunk, that chunk yields []."""
        if not chunks:
            return {}
        k = top_k or settings.pgvector_top_k
        embedder = get_embedder()
        store = get_vector_store()

        texts = [c.get("text", "") for c in chunks]
        try:
            vectors = await embedder.embed(texts)
        except (RAGEmbedFailed, RAGDegraded) as e:
            logger.warning(f"precedent retrieval embed failed: {e}")
            return {str(c.get("id")): [] for c in chunks}

        filters = {"document_id": exclude_document_id} if exclude_document_id else None
        out: Dict[str, List[Dict[str, Any]]] = {}
        for chunk, qvec in zip(chunks, vectors):
            cid = str(chunk.get("id"))
            try:
                hits = await store.hybrid_search(
                    index="rag_compliance_examples",
                    query_text=chunk.get("text", ""),
                    query_vector=qvec,
                    top_k=k,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                    filters=None,
                )
                precedents = [_hit_to_precedent(h) for h in hits]
                # Leakage guard for the eval harness: drop same-document precedents.
                if exclude_document_id:
                    precedents = [p for p in precedents if str(p.get("document_id")) != str(exclude_document_id)]
                out[cid] = precedents
            except RAGDegraded as e:
                logger.warning(f"precedent retrieval degraded for chunk {cid}: {e}")
                out[cid] = []
        return out


_singleton: Optional[PrecedentRetriever] = None


def get_precedent_retriever() -> PrecedentRetriever:
    global _singleton
    if _singleton is None:
        _singleton = PrecedentRetriever()
    return _singleton
```

- [ ] **Step 4: Add `retrieved_examples` to `ComplianceState`**

In `backend/app/services/agents/graph/state.py`, after the `chunk_rules` block (line 23), add:

```python

    # Precedent path: per-chunk retrieved reviewer-decision examples.
    # Set by dispatch_node; analysis_node grades each chunk against these.
    # Shape: { chunk_id: [precedent_dict, ...] }
    retrieved_examples: Dict[str, List[Dict[str, Any]]]
```

- [ ] **Step 5: Run the test; expect pass**

Run: `python -m pytest tests/services/rag/retrievers/test_precedent_retriever_shape.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/rag/retrievers/precedent_retriever.py backend/app/services/agents/graph/state.py backend/tests/services/rag/retrievers/test_precedent_retriever_shape.py
git commit -m "feat(rag): PrecedentRetriever + ComplianceState.retrieved_examples"
```

---

### Task 7: `dispatch_node` → precedent retrieval + KB-empty guard

**Files:**
- Modify: `backend/app/services/agents/graph/nodes.py:90-196` (`dispatch_node`)

The legacy rule-retrieval code stays (it still populates `active_rules` used as a scoring fallback). We add the precedent block, attach `retrieved_examples`, emit `agent_precedent`, and set the KB-empty guard.

- [ ] **Step 1: Add precedent retrieval to `dispatch_node`**

In `backend/app/services/agents/graph/nodes.py`, inside `dispatch_node`, after the metadata block is built (`md["rag_rules_per_chunk"] = ...`, around line 182) and before the `return`, insert:

```python
    # --- Precedent path (primary analysis driver) ---
    retrieved_examples: Dict[str, List[Dict]] = {}
    try:
        from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
        precedent_retriever = get_precedent_retriever()
        retrieved_examples = await precedent_retriever.retrieve_per_chunk(
            chunks=chunks, top_k=settings.pgvector_top_k
        )
    except Exception as e:
        logger.warning(f"Precedent retrieval failed (analysis will find no violations): {e}")
        retrieved_examples = {str(c.get("id")): [] for c in chunks}

    total_precedents = sum(len(v) for v in retrieved_examples.values())
    if total_precedents == 0:
        md["degraded"] = "knowledge_base_empty"
        logger.warning(
            "Knowledge base returned ZERO precedents across all chunks — "
            "analysis will produce no violations. Ingest the precedent corpus "
            "(scripts.ingest_knowledge_base) to enable grading."
        )
    md["precedents_per_chunk"] = {cid: len(v) for cid, v in retrieved_examples.items()}

    if "agent_precedent" not in active_agents:
        active_agents.append("agent_precedent")
```

- [ ] **Step 2: Return `retrieved_examples` from `dispatch_node`**

Change the `dispatch_node` return dict to include the new key (add the `retrieved_examples` line):

```python
    return {
        "active_rules": rules_serializable,
        "chunk_rules": chunk_rules,
        "retrieved_examples": retrieved_examples,
        "active_agents": active_agents,
        "metadata": md,
        "messages": [AIMessage(
            content=(
                f"Brain: Dispatched precedent analysis over {len(chunks)} chunks "
                f"({total_precedents} precedents retrieved). "
                f"Legacy rules loaded: {active_rule_count}."
            )
        )]
    }
```

- [ ] **Step 3: Verify import + node returns the new key (no live DB needed)**

Run: `python -m pytest tests/test_imports.py -v`
Expected: PASS (module imports cleanly; existing import smoke test still green).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/agents/graph/nodes.py
git commit -m "feat(graph): dispatch_node attaches retrieved_examples + KB-empty guard"
```

---

### Task 8: `create_precedent_prompts` + per-chunk `analysis_node` + temperature override

**Files:**
- Modify: `backend/app/services/llm_service.py:118-150` (add `temperature` param)
- Modify: `backend/app/services/preprocessing_service.py` (add `create_precedent_prompts`)
- Modify: `backend/app/services/agents/graph/nodes.py:199-330` (`analysis_node` restructure)
- Test: `backend/tests/services/test_precedent_prompt.py`

- [ ] **Step 1: Write a failing test for the prompt builder**

Create `backend/tests/services/test_precedent_prompt.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.preprocessing_service import ContextEngineeringService


def test_precedent_prompt_includes_examples_and_new_section():
    svc = ContextEngineeringService(db=None)
    precedents = [
        {
            "reviewer_name": "Shailja Saklani/Pune HO/Legal Compliance and FPU/Life",
            "chunk_text": "A Rs.1 crore term plan is like any other plan with high sum assured.",
            "comment_text": "Logic is incorrect; do not claim high sum assured.",
            "violation_category": "legal language",
            "severity": "critical",
            "final_text_chunk": "A Rs.1 crore term plan is like any other term plan.",
        }
    ]
    content = "Buy our guaranteed 1 crore plan today!"
    prompt = svc.create_precedent_prompts(content, precedents)
    assert "Shailja Saklani" in prompt
    assert "Logic is incorrect" in prompt
    assert "legal language" in prompt
    assert "critical" in prompt
    assert "NEW DOCUMENT SECTION" in prompt
    assert content in prompt
    assert "current_text" in prompt


def test_precedent_prompt_handles_empty_precedents():
    svc = ContextEngineeringService(db=None)
    prompt = svc.create_precedent_prompts("some text", [])
    assert "NEW DOCUMENT SECTION" in prompt
    assert "some text" in prompt
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/test_precedent_prompt.py -v`
Expected: FAIL — `AttributeError: 'ContextEngineeringService' object has no attribute 'create_precedent_prompts'`.

- [ ] **Step 3: Add `create_precedent_prompts`**

In `backend/app/services/preprocessing_service.py`, add this method to `ContextEngineeringService` (after `create_compliance_prompts`, before the alias at the bottom):

```python
    def create_precedent_prompts(self, content: str, precedents: List[Dict]) -> str:
        """Build a few-shot precedent-imitation prompt.

        The examples are PAST reviewer decisions (ground truth). The model
        imitates their tone/severity/phrasing on the NEW document section.
        Output still requires `current_text` (verbatim from the new section) so
        frontend highlighting keeps working. Severity/category use the new
        precedent vocabulary.
        """
        examples_text = ""
        for i, p in enumerate(precedents, 1):
            examples_text += (
                f"\n--- EXAMPLE {i} ---\n"
                f"Reviewer: {p.get('reviewer_name') or 'Unknown'}\n"
                f"Original text: {p.get('chunk_text') or ''}\n"
                f"Compliance comment: {p.get('comment_text') or ''}\n"
                f"Violation type: {p.get('violation_category') or 'other'}\n"
                f"Severity: {p.get('severity') or 'informational'}\n"
            )
            if p.get("final_text_chunk"):
                examples_text += f"Approved rewrite: {p['final_text_chunk']}\n"

        prompt = f"""You are a senior Bajaj Allianz Life Insurance compliance reviewer.
Below are REAL past review decisions made by senior reviewers. They are the
ground truth for how this team flags compliance issues. Imitate their tone,
severity calibration and phrasing. Do NOT introduce violation categories or
terminology that do not appear in the examples.

PAST REVIEWER DECISIONS (ground truth):
{examples_text}

NEW DOCUMENT SECTION (review this against the patterns above):
{content}

For each compliance issue you find in the NEW DOCUMENT SECTION, output a violation with:
- current_text — the EXACT problematic phrase copied verbatim from the NEW DOCUMENT SECTION above (no paraphrase)
- suggested_fix — a compliant rewrite, informed by the "Approved rewrite" patterns when present
- category — one of: terminology issue, legal language, missing reference, disclaimer issue, other
- severity — one of: critical, moderate, informational
- description — one sentence describing the issue, in the reviewers' style
- confidence — your 0.0-1.0 confidence that a senior reviewer would flag this

Rules:
- Only flag issues that the example reviewers would plausibly flag. If nothing matches, return an empty violations list.
- Do not invent rule IDs; leave rule_id null.
- Output ONLY valid JSON matching the required schema."""
        return prompt
```

- [ ] **Step 4: Run the prompt test; expect pass**

Run: `python -m pytest tests/services/test_precedent_prompt.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the additive `temperature` param to the LLM service**

In `backend/app/services/llm_service.py`, change the `generate_structured_response` signature (line 119-128) to add `temperature`:

```python
    async def generate_structured_response(
        self,
        prompt: str,
        output_model: Type[T],
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        execution_id: str = None,
        db: Session = None,
        tool_name: str = "llm_structured",
        temperature: float = 0.2,
    ) -> T:
```

Then in the `with_raw_response.create(...)` call (line 145-150), replace the hardcoded `temperature=0.2` with the param:

```python
                response_wrapper = await self.client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=current_messages,
                    temperature=temperature,
                    response_format={"type": "json_object"}
                )
```

- [ ] **Step 6: Restructure `analysis_node` to per-chunk precedent grading**

In `backend/app/services/agents/graph/nodes.py`, replace the entire body of `analysis_node` (lines 199-330) with:

```python
@traceable(run_type="chain", name="graph.analysis_node")
async def analysis_node(state: ComplianceState) -> Dict:
    """
    Compliance Specialist Node (precedent path): grades each chunk against its
    retrieved reviewer-decision precedents, in parallel. One LLM call per chunk
    at temperature 0. Output contract (ComplianceAnalysisResult) is unchanged.
    """
    logger.info("Node: Analysis (precedent) running...")

    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.llm_service import llm_service
    from app.services.agents.validators import validate_agent_output
    from app.schemas.compliance_schemas import ComplianceAnalysisResult
    from app.models.agent_execution import AgentExecution
    from app.database import SessionLocal

    chunks_data = state.get("chunks", [])
    retrieved = state.get("retrieved_examples") or {}
    submission_id = state.get("submission_id")
    user_id = state.get("user_id")

    new_violations: List[Dict] = []

    CORRECTIVE_SUFFIX = (
        "\n\nYour previous output had invalid fields. Ensure severity is one of "
        "critical/moderate/informational, category is non-empty, description is "
        "at least 10 characters, and confidence is between 0 and 1."
    )

    async def grade_chunk(chunk_data: Dict) -> List[Dict]:
        chunk_id = chunk_data.get("id")
        chunk_index = chunk_data.get("chunk_index")
        chunk_text = chunk_data.get("text", "")
        precedents = retrieved.get(str(chunk_id), [])
        if not precedents:
            return []  # Decision 5: no precedents → no violations for this chunk.

        task_db = SessionLocal()
        kept: List[Dict] = []
        try:
            context_service = ContextEngineeringService(task_db)
            execution = AgentExecution(
                agent_type="precedent",
                session_id=uuid.UUID(submission_id) if submission_id else None,
                user_id=uuid.UUID(user_id) if user_id else None,
                status="running",
                input_data={
                    "chunk_index": chunk_index,
                    "text_preview": chunk_text[:100],
                    "precedents_count": len(precedents),
                },
            )
            task_db.add(execution)
            task_db.commit()

            prompt = context_service.create_precedent_prompts(chunk_text, precedents)
            system_prompt = (
                "You are a senior Bajaj Allianz compliance reviewer imitating past "
                "reviewer decisions. Return ONLY valid JSON matching the schema."
            )

            async def _call(p: str) -> ComplianceAnalysisResult:
                return await llm_service.generate_structured_response(
                    prompt=p,
                    output_model=ComplianceAnalysisResult,
                    system_prompt=system_prompt,
                    execution_id=str(execution.id),
                    db=task_db,
                    tool_name="precedent_analysis",
                    temperature=0.0,
                )

            result = await _call(prompt)
            raw = [v.model_dump() for v in result.violations]
            all_ok = all(validate_agent_output(v)[0] for v in raw)
            if not all_ok:
                result = await _call(prompt + CORRECTIVE_SUFFIX)
                raw = [v.model_dump() for v in result.violations]

            for v in raw:
                ok, errs = validate_agent_output(v)
                if not ok:
                    _log_grade_error(chunk_id, v, errs)
                    continue
                v["chunk_id"] = str(chunk_id)
                v["chunk_index"] = chunk_index
                loc = f"chunk:{chunk_id}"
                meta = chunk_data.get("metadata", {})
                if meta.get("page_number"):
                    loc += f":page:{meta['page_number']}"
                v["location"] = loc
                kept.append(v)

            execution.status = "completed"
            execution.output_data = {"violations": kept}
            task_db.commit()
        except Exception as e:
            logger.error(f"Precedent grading failed (chunk {chunk_index}): {e}")
        finally:
            task_db.close()
        return kept

    tasks = [grade_chunk(c) for c in chunks_data]
    if tasks:
        logger.info(f"Running {len(tasks)} per-chunk precedent grading tasks...")
        results = await asyncio.gather(*tasks)
        for res in results:
            new_violations.extend(res)

    return {
        "violations": new_violations,
        "messages": [AIMessage(content=f"Analysis: Found {len(new_violations)} violations (precedent path).")]
    }


def _log_grade_error(chunk_id, violation: Dict, errors: List[str]) -> None:
    import os
    import json as _json
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "grade_errors.log"), "a", encoding="utf-8") as f:
        f.write(_json.dumps({"chunk_id": str(chunk_id), "errors": errors, "violation": violation}) + "\n")
```

Note: `validate_agent_output` is implemented in Task 9; this node imports it. Implement Task 9 before running the live graph (Task 15). The pure prompt + LLM-param changes here are independently testable now.

- [ ] **Step 7: Run prompt test + import smoke; expect pass**

Run: `python -m pytest tests/services/test_precedent_prompt.py tests/test_imports.py -v`
Expected: PASS. (Import of `validators` resolves once Task 9 lands; if running strictly in order, complete Task 9 Step 3 first, then re-run.)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/llm_service.py backend/app/services/preprocessing_service.py backend/app/services/agents/graph/nodes.py backend/tests/services/test_precedent_prompt.py
git commit -m "feat(graph): per-chunk precedent analysis_node + create_precedent_prompts + temp override"
```

---

### Task 9: `validate_agent_output` + retry/logging

**Files:**
- Create: `backend/app/services/agents/validators.py`
- Test: `backend/tests/services/agents/test_validators.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/agents/test_validators.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.services.agents.validators import validate_agent_output


def _valid():
    return {
        "category": "legal language",
        "severity": "critical",
        "description": "This makes an unsupported guarantee claim.",
        "confidence": 0.9,
    }


def test_valid_output_passes():
    ok, errs = validate_agent_output(_valid())
    assert ok is True
    assert errs == []


def test_non_dict_fails():
    ok, errs = validate_agent_output("nope")
    assert ok is False
    assert errs


def test_empty_category_fails():
    v = _valid(); v["category"] = ""
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("category" in e for e in errs)


def test_bad_severity_fails():
    v = _valid(); v["severity"] = "high"  # not in new vocab
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("severity" in e for e in errs)


def test_short_description_fails():
    v = _valid(); v["description"] = "too short"
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("description" in e for e in errs)


def test_confidence_out_of_range_fails():
    v = _valid(); v["confidence"] = 1.5
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("confidence" in e for e in errs)


def test_violation_found_must_be_bool_if_present():
    v = _valid(); v["violation_found"] = "yes"
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("violation_found" in e for e in errs)


def test_score_impact_in_range_if_present():
    v = _valid(); v["score_impact"] = 2.0
    ok, errs = validate_agent_output(v)
    assert ok is False
    assert any("score_impact" in e for e in errs)
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/agents/test_validators.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the validator**

Create `backend/app/services/agents/validators.py`:

```python
"""Output validation for the precedent analysis path.

validate_agent_output checks a single violation-like dict against the new
precedent vocabulary. The analysis node uses it to retry once and then
log+continue — it never raises into the graph.
"""
from __future__ import annotations

from typing import Any, List, Tuple

ALLOWED_SEVERITIES = {"critical", "moderate", "informational"}


def validate_agent_output(output: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(output, dict):
        return False, ["output is not a dict"]

    vf = output.get("violation_found")
    if vf is not None and not isinstance(vf, bool):
        errors.append("violation_found must be a boolean if present")

    category = output.get("category")
    if not isinstance(category, str) or not category.strip():
        errors.append("category must be a non-empty string")

    severity = output.get("severity")
    if severity not in ALLOWED_SEVERITIES:
        errors.append(f"severity must be one of {sorted(ALLOWED_SEVERITIES)}")

    description = output.get("description")
    if not isinstance(description, str) or len(description.strip()) < 10:
        errors.append("description must be a string of at least 10 characters")

    for key in ("score_impact", "confidence"):
        if key in output and output[key] is not None:
            val = output[key]
            if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
                errors.append(f"{key} must be a number in [0, 1]")

    return (len(errors) == 0), errors
```

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/services/agents/test_validators.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents/validators.py backend/tests/services/agents/test_validators.py
git commit -m "feat(agents): validate_agent_output for precedent path"
```

---

### Task 10: Extend `SEVERITY_WEIGHTS`

**Files:**
- Modify: `backend/app/services/agents/compliance/scoring.py:18-23`
- Test: `backend/tests/services/agents/compliance/test_scoring_weights.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/agents/compliance/test_scoring_weights.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from app.services.agents.compliance.scoring import ScoringService


def test_existing_weights_unchanged():
    w = ScoringService.SEVERITY_WEIGHTS
    assert w["critical"] == 20
    assert w["high"] == 10
    assert w["medium"] == 5
    assert w["low"] == 2


def test_new_vocab_weights_added():
    w = ScoringService.SEVERITY_WEIGHTS
    assert w["moderate"] == 8
    assert w["informational"] == 2


def test_critical_still_triggers_failed_status():
    # _get_status fails on any critical violation.
    status = ScoringService._get_status([{"severity": "critical"}], overall_score=95.0)
    assert status == "failed"


def test_informational_does_not_force_fail():
    status = ScoringService._get_status([{"severity": "informational"}], overall_score=95.0)
    assert status == "passed"
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/agents/compliance/test_scoring_weights.py -v`
Expected: FAIL — `KeyError: 'moderate'`.

- [ ] **Step 3: Extend the dict only**

In `backend/app/services/agents/compliance/scoring.py`, replace lines 18-23:

```python
    SEVERITY_WEIGHTS = {
        "critical": 20,
        "high": 10,
        "medium": 5,
        "low": 2,
        "moderate": 8,        # added — new precedent vocab
        "informational": 2,   # added — new precedent vocab
    }
```

- [ ] **Step 4: Run it; expect pass**

Run: `python -m pytest tests/services/agents/compliance/test_scoring_weights.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents/compliance/scoring.py backend/tests/services/agents/compliance/test_scoring_weights.py
git commit -m "feat(scoring): extend SEVERITY_WEIGHTS with moderate/informational"
```

---

### Task 11: Eval replay harness + metric helpers

**Files:**
- Create: `backend/scripts/eval_precedent_replay.py`
- Test: `backend/tests/scripts/test_eval_metrics.py`

The pure metric helpers are unit-tested; the live run is a CLI invocation (Task 15).

- [ ] **Step 1: Write the failing test for metric helpers**

Create `backend/tests/scripts/test_eval_metrics.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.eval_precedent_replay import (
    split_by_hash,
    precision_recall,
    count_missed_criticals,
)


def test_split_is_deterministic_and_disjoint():
    files = [f"f{i}.json" for i in range(100)]
    train1, ev1 = split_by_hash(files, eval_frac=0.1)
    train2, ev2 = split_by_hash(files, eval_frac=0.1)
    assert train1 == train2 and ev1 == ev2          # deterministic
    assert set(train1).isdisjoint(set(ev1))          # disjoint
    assert len(train1) + len(ev1) == 100
    assert 5 <= len(ev1) <= 20                        # ~10%


def test_precision_recall_basic():
    # predicted flags on anchors {a,b}; real on {b,c}. TP=1 (b), FP=1 (a), FN=1 (c)
    p, r = precision_recall(predicted={"a", "b"}, actual={"b", "c"})
    assert round(p, 3) == 0.5
    assert round(r, 3) == 0.5


def test_precision_recall_empty_predicted():
    p, r = precision_recall(predicted=set(), actual={"b"})
    assert p == 0.0
    assert r == 0.0


def test_precision_recall_empty_actual():
    p, r = precision_recall(predicted={"a"}, actual=set())
    assert p == 0.0
    assert r == 1.0   # nothing to recall → recall defined as 1.0


def test_missed_criticals():
    real = [{"anchor": "x", "severity": "critical"}, {"anchor": "y", "severity": "moderate"}]
    matched_anchors = {"y"}
    assert count_missed_criticals(real, matched_anchors) == 1
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/scripts/test_eval_metrics.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the eval harness**

Create `backend/scripts/eval_precedent_replay.py`:

```python
"""Leakage-safe replay eval for the precedent analysis path.

Deterministically split the _rl corpus by hash(source_file) into train/eval.
Ingest only train into a fresh KB; evaluate on eval. No eval doc's own
precedents can be retrieved (retrieval also excludes same document_id).

Pure metric helpers are unit-tested; the live run needs DB + LLM + embedder.

Usage:
  cd backend && python -m scripts.eval_precedent_replay --folder ../dataset/Dataset/Dataset/dataset_2.1_rl --eval-frac 0.1
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from typing import Dict, List, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("eval_precedent_replay")


# ----------------------------- pure helpers -----------------------------

def split_by_hash(files: List[str], eval_frac: float = 0.1) -> Tuple[List[str], List[str]]:
    """Deterministic split: a file is 'eval' if md5(name) mod 1000 < eval_frac*1000."""
    cutoff = int(eval_frac * 1000)
    train, ev = [], []
    for f in files:
        h = int(hashlib.md5(os.path.basename(f).encode("utf-8")).hexdigest(), 16) % 1000
        (ev if h < cutoff else train).append(f)
    return train, ev


def precision_recall(predicted: Set[str], actual: Set[str]) -> Tuple[float, float]:
    if not predicted and not actual:
        return 0.0, 0.0
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else 0.0
    recall = (tp / len(actual)) if actual else 1.0
    return precision, recall


def count_missed_criticals(real_comments: List[Dict], matched_anchors: Set[str]) -> int:
    return sum(
        1
        for c in real_comments
        if c.get("severity") == "critical" and c.get("anchor") not in matched_anchors
    )


# ----------------------------- live run -----------------------------

async def _run(folder: str, eval_frac: float) -> Dict:
    from app.services.knowledge_base_ingestion import (
        get_kb_ingestion_service,
        parse_compliance_comments,
        classify_severity,
        align_comment_to_chunk,
    )
    from app.services.rag.retrievers.precedent_retriever import get_precedent_retriever
    from app.services.preprocessing_service import ContextEngineeringService
    from app.services.llm_service import llm_service
    from app.schemas.compliance_schemas import ComplianceAnalysisResult
    from app.services.rag.factory import get_embedder
    from app.config import settings

    files = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json"))
    train, ev = split_by_hash(files, eval_frac)
    logger.info(f"Split: {len(train)} train / {len(ev)} eval")

    svc = get_kb_ingestion_service()
    # Ingest only the train split (caller is expected to start from a fresh KB).
    inserted = 0
    batch: List[Dict] = []
    from app.services.rag.indexers.compliance_examples_indexer import upsert_examples
    for path in train:
        try:
            batch.extend(svc.parse_file(path)["rows"])
            if len(batch) >= settings.kb_batch_size:
                inserted += await upsert_examples(batch); batch = []
        except Exception as e:
            logger.warning(f"train ingest skip {os.path.basename(path)}: {e}")
    inserted += await upsert_examples(batch)
    logger.info(f"Ingested {inserted} train precedents")

    retriever = get_precedent_retriever()
    embedder = get_embedder()
    ctx = ContextEngineeringService(db=None)

    docs_evaluated = 0
    gen_total = real_total = 0
    micro_pred: Set[str] = set()
    micro_actual: Set[str] = set()
    missed_criticals = 0
    sims: List[float] = []

    def _chunk(content: str) -> List[str]:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        sp = RecursiveCharacterTextSplitter(
            chunk_size=settings.kb_chunk_size, chunk_overlap=settings.kb_chunk_overlap
        )
        return [c for c in sp.split_text(content or "") if c.strip()]

    def _cos(a, b) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    for path in ev:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        inp = data.get("input") or {}
        meta = data.get("metadata") or {}
        draft = inp.get("draft_text") or ""
        if not draft.strip():
            continue
        document_id = str(meta.get("document_id") or "")
        chunks = _chunk(draft)
        chunk_objs = [{"id": f"c{i}", "text": t, "chunk_index": i} for i, t in enumerate(chunks)]

        # Ground truth from the real comments.
        real = []
        for pc in parse_compliance_comments(inp.get("compliance_comments"), os.path.basename(path)):
            idx, _ = align_comment_to_chunk(pc.anchor, chunks, settings.kb_min_fuzzy_score)
            real.append({"anchor": pc.anchor, "severity": classify_severity(pc.reviewer),
                         "chunk_idx": idx, "comment": pc.comment})
        real_anchors = {r["anchor"] for r in real if r["chunk_idx"] is not None}
        real_chunks = {r["chunk_idx"] for r in real if r["chunk_idx"] is not None}

        # Generated, with same-document leakage excluded.
        retrieved = await retriever.retrieve_per_chunk(
            chunk_objs, top_k=settings.pgvector_top_k, exclude_document_id=document_id
        )
        gen_chunks: Set[int] = set()
        gen_descriptions: List[Tuple[int, str]] = []
        for cobj in chunk_objs:
            preds = retrieved.get(cobj["id"], [])
            if not preds:
                continue
            prompt = ctx.create_precedent_prompts(cobj["text"], preds)
            result = await llm_service.generate_structured_response(
                prompt=prompt, output_model=ComplianceAnalysisResult,
                system_prompt="Imitate the example reviewers. JSON only.", temperature=0.0,
            )
            if result.violations:
                gen_chunks.add(cobj["chunk_index"])
                for v in result.violations:
                    gen_descriptions.append((cobj["chunk_index"], v.description or ""))

        gen_total += len(gen_descriptions)
        real_total += len(real)
        # Micro presence sets keyed by (doc, chunk_idx).
        micro_pred |= {f"{document_id}:{ci}" for ci in gen_chunks}
        micro_actual |= {f"{document_id}:{ci}" for ci in real_chunks}
        matched_anchors = {r["anchor"] for r in real if r["chunk_idx"] in gen_chunks}
        missed_criticals += count_missed_criticals(real, matched_anchors)

        # Comment cosine: match generated descriptions to real comments on the same chunk.
        for ci, desc in gen_descriptions:
            same = [r["comment"] for r in real if r["chunk_idx"] == ci]
            if not same or not desc.strip():
                continue
            try:
                embs = await embedder.embed([desc, same[0]])
                sims.append(_cos(embs[0], embs[1]))
            except Exception:
                pass
        docs_evaluated += 1

    precision, recall = precision_recall(micro_pred, micro_actual)
    metrics = {
        "docs_evaluated": docs_evaluated,
        "train_files": len(train),
        "eval_files": len(ev),
        "precision_presence": round(precision, 4),
        "recall_presence": round(recall, 4),
        "missed_criticals": missed_criticals,
        "mean_comment_cosine": round(sum(sims) / len(sims), 4) if sims else None,
        "generated_violations": gen_total,
        "real_violations": real_total,
    }
    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "eval_replay.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    logger.info("Eval metrics:\n" + json.dumps(metrics, indent=2))
    return metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Leakage-safe precedent replay eval")
    p.add_argument("--folder", required=True)
    p.add_argument("--eval-frac", type=float, default=0.1)
    args = p.parse_args()
    asyncio.run(_run(args.folder, args.eval_frac))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the metric tests; expect pass**

Run: `python -m pytest tests/scripts/test_eval_metrics.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/eval_precedent_replay.py backend/tests/scripts/test_eval_metrics.py
git commit -m "feat(eval): leakage-safe precedent replay harness + metric helpers"
```

---

### Task 12: Vector projection service + endpoint

**Files:**
- Create: `backend/app/services/vector_projection.py`
- Modify: `backend/app/api/routes/knowledge_base.py` (add `/projection`)
- Test: `backend/tests/services/test_vector_projection_helpers.py`

- [ ] **Step 1: Write the failing test for the pure helpers**

Create `backend/tests/services/test_vector_projection_helpers.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.vector_projection import _parse_pgvector_literal, _cache_key, project_2d


def test_parse_pgvector_literal():
    assert _parse_pgvector_literal("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]
    assert _parse_pgvector_literal("[]") == []


def test_cache_key_signature():
    k = _cache_key("umap", n_examples=10, n_rules=5, n_source_docs=3, cap=2000)
    assert k == "umap:10:5:3:2000"


def test_project_2d_returns_xy_per_row():
    # 6 simple 4-dim vectors → PCA fallback path works for small n.
    vecs = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1], [1, 1, 0, 0], [0, 0, 1, 1]]
    pts = project_2d(vecs, method="pca")
    assert len(pts) == 6
    assert all(len(p) == 2 for p in pts)


def test_project_2d_empty():
    assert project_2d([], method="umap") == []
```

- [ ] **Step 2: Run it; expect failure**

Run: `python -m pytest tests/services/test_vector_projection_helpers.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the projection service**

Create `backend/app/services/vector_projection.py`:

```python
"""2-D projection of stored embeddings for the knowledge-base visualization.

A single UMAP (or PCA fallback) fit over ALL selected points stacked into one
matrix, so the three indexes share a comparable space. Cached at module level
keyed on per-table counts (UMAP is too slow to run per page load).
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Dict[str, Any]] = {}

# Tables to project, with the descriptive columns to surface per point.
_SOURCES = [
    ("rag_compliance_examples", "comment_text", ["violation_category", "severity", "reviewer_name"]),
    ("rag_rules", "rule_text", ["category", "severity"]),
    ("rag_source_docs", "text", ["regulator"]),
]


def _parse_pgvector_literal(s: str) -> List[float]:
    s = (s or "").strip()
    if not s or s == "[]":
        return []
    return [float(x) for x in s.strip("[]").split(",") if x.strip()]


def _cache_key(method: str, n_examples: int, n_rules: int, n_source_docs: int, cap: int) -> str:
    return f"{method}:{n_examples}:{n_rules}:{n_source_docs}:{cap}"


def project_2d(vectors: List[List[float]], method: str = "umap") -> List[List[float]]:
    """Project N D-dim vectors to N 2-D points. PCA fallback if UMAP missing or n<4."""
    if not vectors:
        return []
    import numpy as np

    arr = np.array(vectors, dtype="float32")
    n = arr.shape[0]
    use_umap = method == "umap" and n >= 4
    if use_umap:
        try:
            import umap  # type: ignore
            reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=min(15, n - 1))
            return reducer.fit_transform(arr).tolist()
        except Exception as e:
            logger.warning(f"UMAP unavailable/failed ({e}); falling back to PCA")
    # PCA fallback (sklearn).
    from sklearn.decomposition import PCA

    comps = 2 if n >= 2 else 1
    coords = PCA(n_components=comps).fit_transform(arr)
    if comps == 1:
        return [[float(c[0]), 0.0] for c in coords]
    return coords.tolist()


def _count(db, table: str) -> int:
    return int(db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def compute_projection(method: str = "umap", refresh: bool = False) -> Dict[str, Any]:
    cap = settings.viz_points_per_index
    db = SessionLocal()
    try:
        counts = {tbl: _count(db, tbl) for tbl, _, _ in _SOURCES}
        key = _cache_key(
            method,
            counts.get("rag_compliance_examples", 0),
            counts.get("rag_rules", 0),
            counts.get("rag_source_docs", 0),
            cap,
        )
        if not refresh and key in _CACHE:
            return _CACHE[key]

        ids: List[str] = []
        index_of: List[str] = []
        labels: List[Optional[str]] = []
        snippets: List[str] = []
        extras: List[Dict[str, Any]] = []
        vectors: List[List[float]] = []

        for table, snippet_col, extra_cols in _SOURCES:
            cols = ", ".join(["id", "embedding::text"] + [snippet_col] + extra_cols)
            rows = db.execute(
                text(f"SELECT {cols} FROM {table} ORDER BY random() LIMIT :cap"),
                {"cap": cap},
            ).all()
            for r in rows:
                vec = _parse_pgvector_literal(r[1])
                if not vec:
                    continue
                vectors.append(vec)
                ids.append(str(r[0]))
                index_of.append(table)
                snippets.append((r[2] or "")[:160])
                extras.append({c: r[3 + i] for i, c in enumerate(extra_cols)})
                labels.append(extras[-1].get("violation_category") or extras[-1].get("category") or extras[-1].get("regulator"))

        coords = project_2d(vectors, method=method)
        points = []
        for i, (x, y) in enumerate(coords):
            ex = extras[i]
            points.append(
                {
                    "id": ids[i],
                    "index": index_of[i],
                    "x": round(float(x), 4),
                    "y": round(float(y), 4),
                    "category": ex.get("violation_category") or ex.get("category"),
                    "severity": ex.get("severity"),
                    "reviewer_name": ex.get("reviewer_name"),
                    "label": labels[i],
                    "snippet": snippets[i],
                }
            )

        from datetime import datetime
        result = {
            "method": "umap" if (method == "umap" and len(vectors) >= 4) else "pca",
            "computed_at": datetime.utcnow().isoformat(),
            "counts": counts,
            "points": points,
        }
        _CACHE[key] = result
        return result
    finally:
        db.close()
```

- [ ] **Step 4: Add the `/projection` endpoint**

In `backend/app/api/routes/knowledge_base.py`, add to the imports:

```python
from fastapi import Query
from app.services.vector_projection import compute_projection
```

And append a route:

```python
@router.get("/projection")
async def knowledge_base_projection(
    method: str = Query("umap", pattern="^(umap|pca)$"),
    refresh: bool = False,
):
    try:
        return compute_projection(method=method, refresh=refresh)
    except Exception as e:
        logger.error(f"Projection failed: {e}")
        raise HTTPException(status_code=500, detail=f"Projection failed: {e}")
```

- [ ] **Step 5: Run the helper tests; expect pass**

Run: `python -m pytest tests/services/test_vector_projection_helpers.py -v`
Expected: PASS (4 tests). (Requires `numpy`/`scikit-learn` from Task 2 installed.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/vector_projection.py backend/app/api/routes/knowledge_base.py backend/tests/services/test_vector_projection_helpers.py
git commit -m "feat(viz): vector projection service + /knowledge-base/projection endpoint"
```

---

### Task 13: Frontend — knowledge-base page, scatter, api/types, sidebar

**Files:**
- Modify: `frontend/lib/types.ts` (add types)
- Modify: `frontend/lib/api.ts` (add fetch fn)
- Create: `frontend/components/knowledge-base/VectorSpaceScatter.tsx`
- Create: `frontend/app/(workspace)/knowledge-base/page.tsx`
- Modify: `frontend/components/workspace/Sidebar.tsx` (one nav entry)

No frontend test runner exists; verification is `npm run typecheck` + `npm run build`.

- [ ] **Step 1: Add projection types**

In `frontend/lib/types.ts`, append:

```typescript
export interface ProjectionPoint {
  id: string;
  index: "rag_compliance_examples" | "rag_rules" | "rag_source_docs" | string;
  x: number;
  y: number;
  category?: string | null;
  severity?: string | null;
  reviewer_name?: string | null;
  label?: string | null;
  snippet?: string | null;
}

export interface ProjectionResponse {
  method: "umap" | "pca" | string;
  computed_at: string;
  counts: Record<string, number>;
  points: ProjectionPoint[];
}
```

- [ ] **Step 2: Add the API client function**

In `frontend/lib/api.ts`, add `ProjectionResponse` to the type import block (lines 6-11):

```typescript
import type {
  ComplianceResults,
  DashboardSummary,
  ProjectionResponse,
  Rule,
  Submission,
} from "./types";
```

And append a section:

```typescript
/* ---------- knowledge base ---------- */
export async function getKnowledgeBaseProjection(
  method: "umap" | "pca" = "umap"
): Promise<ProjectionResponse> {
  return jsonFetch(`${base()}/knowledge-base/projection?method=${method}`);
}
```

- [ ] **Step 3: Create the scatter component**

Create `frontend/components/knowledge-base/VectorSpaceScatter.tsx`:

```tsx
"use client";
import * as React from "react";
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { ProjectionPoint } from "@/lib/types";

const INDEX_META: Record<string, { label: string; color: string }> = {
  rag_compliance_examples: { label: "Precedents", color: "hsl(var(--primary))" },
  rag_rules: { label: "Rules", color: "#b45309" },
  rag_source_docs: { label: "Source docs", color: "#0f766e" },
};

interface Props {
  points: ProjectionPoint[];
}

export function VectorSpaceScatter({ points }: Props) {
  const [severity, setSeverity] = React.useState<string | null>(null);

  const severities = React.useMemo(
    () => Array.from(new Set(points.map((p) => p.severity).filter(Boolean))) as string[],
    [points]
  );

  const groups = React.useMemo(() => {
    const filtered = severity ? points.filter((p) => p.severity === severity) : points;
    const byIndex: Record<string, ProjectionPoint[]> = {};
    for (const p of filtered) (byIndex[p.index] ??= []).push(p);
    return byIndex;
  }, [points, severity]);

  if (points.length === 0) {
    return (
      <div className="flex h-96 items-center justify-center rounded-md border border-border bg-surface text-sm text-muted-foreground">
        No vectors to plot. Ingest the knowledge base first.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border bg-surface p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-serif text-lg">Vector memory space</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            2-D projection of precedents, rules and source passages.
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Chip active={severity === null} onClick={() => setSeverity(null)} label="All" />
          {severities.map((s) => (
            <Chip key={s} active={severity === s} onClick={() => setSeverity(s)} label={s} />
          ))}
        </div>
      </div>
      <div className="mt-4 h-96">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
            <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" />
            <XAxis type="number" dataKey="x" tick={{ fontSize: 10 }} name="x" />
            <YAxis type="number" dataKey="y" tick={{ fontSize: 10 }} name="y" />
            <ZAxis range={[30, 30]} />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              contentStyle={{
                background: "hsl(var(--background))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 6,
                fontSize: 12,
                maxWidth: 320,
              }}
              content={<PointTooltip />}
            />
            <Legend />
            {Object.entries(groups).map(([index, pts]) => (
              <Scatter
                key={index}
                name={INDEX_META[index]?.label ?? index}
                data={pts}
                fill={INDEX_META[index]?.color ?? "hsl(var(--muted-foreground))"}
                fillOpacity={0.7}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Chip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-sm border px-2 py-0.5 text-[11px] transition-colors " +
        (active
          ? "border-primary text-primary"
          : "border-border text-muted-foreground hover:text-foreground")
      }
    >
      {label}
    </button>
  );
}

function PointTooltip({ active, payload }: { active?: boolean; payload?: { payload: ProjectionPoint }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="space-y-1">
      <div className="font-medium">{INDEX_META[p.index]?.label ?? p.index}</div>
      {p.category && <div className="text-xs">Category: {p.category}</div>}
      {p.severity && <div className="text-xs">Severity: {p.severity}</div>}
      {p.reviewer_name && <div className="text-xs">Reviewer: {p.reviewer_name}</div>}
      {p.snippet && <div className="text-xs text-muted-foreground">{p.snippet}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Create the page**

Create `frontend/app/(workspace)/knowledge-base/page.tsx`:

```tsx
import { getKnowledgeBaseProjection } from "@/lib/api";
import { VectorSpaceScatter } from "@/components/knowledge-base/VectorSpaceScatter";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";
import type { ProjectionResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function KnowledgeBasePage() {
  let proj: ProjectionResponse | null = null;
  let err: string | null = null;
  try {
    proj = await getKnowledgeBaseProjection("umap");
  } catch (e) {
    err = (e as Error).message;
  }

  const counts = proj?.counts ?? {};
  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <Masthead
        edition="Vector Memory · §04"
        title={<>Knowledge <span className="italic">Base</span></>}
        subtitle="How past reviewer decisions, rules and source passages sit in embedding space. Precedents drive the few-shot compliance analysis."
        meta={
          <>
            <MetaItem label="Precedents" value={counts["rag_compliance_examples"] ?? 0} />
            <MetaItem label="Rules" value={counts["rag_rules"] ?? 0} />
            <MetaItem label="Source docs" value={counts["rag_source_docs"] ?? 0} />
            <MetaItem label="Projection" value={proj?.method ?? "—"} />
          </>
        }
      />
      {err ? (
        <div className="rounded-md border border-border bg-surface p-8">
          <div className="micro-label text-sev-critical">API unreachable</div>
          <h2 className="mt-2 font-serif text-2xl">Projection couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : (
        <div className="mt-8">
          <VectorSpaceScatter points={proj?.points ?? []} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Add the Sidebar entry**

In `frontend/components/workspace/Sidebar.tsx`, add `Boxes` to the lucide import (line 4-12), then add an item to the "Insights" section so it reads:

```tsx
  {
    title: "Insights",
    items: [
      { label: "Dashboard", href: "/dashboard", icon: <LineChart className="h-3.5 w-3.5" />, kbd: "D" },
      { label: "Knowledge base", href: "/knowledge-base", icon: <Boxes className="h-3.5 w-3.5" />, kbd: "K" },
    ],
  },
```

- [ ] **Step 6: Typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: typecheck passes; build completes with the new `/knowledge-base` route listed.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/api.ts frontend/components/knowledge-base/VectorSpaceScatter.tsx "frontend/app/(workspace)/knowledge-base/page.tsx" frontend/components/workspace/Sidebar.tsx
git commit -m "feat(frontend): knowledge-base vector-space page + scatter + sidebar entry"
```

---

### Task 14: README — Vector Memory System section

**Files:**
- Modify: `README.md` (append a section; touch nothing existing)

- [ ] **Step 1: Append the section**

Add to the end of `README.md`:

```markdown
## Vector Memory System (Precedent Compliance Engine)

The agent grades documents by imitating **real past reviewer decisions**, not
just static rules. Precedents `(draft chunk → reviewer comment → anchor → final
rewrite → reviewer name → severity)` are mined from the reviewed corpus and
stored in a dedicated pgvector index, `rag_compliance_examples`, alongside the
existing `rag_rules` / `rag_chunks` / `rag_source_docs` indexes.

**How it differs from `rag_rules`:** rules are abstract policy statements;
precedents are concrete, human-made review decisions used as few-shot examples.
On the analysis path, `dispatch_node` retrieves the top-K most similar
precedents per document chunk and `analysis_node` grades each chunk against them
at temperature 0. (The rule CRUD/generation endpoints and rule retrieval remain
fully operational; they are simply no longer the analysis driver.)

**Vocabulary:** severities `critical | moderate | informational`; categories
`terminology issue | legal language | missing reference | disclaimer issue |
other`.

### Ingest the knowledge base

```bash
# In the backend container (dataset is mounted read-only at /app/dataset):
docker exec compliance-backend python -m scripts.ingest_knowledge_base --limit 50 --preview
docker exec compliance-backend python -m scripts.ingest_knowledge_base

# Or on the host (Postgres exposed on localhost:5432):
cd backend && python -m scripts.ingest_knowledge_base --folder ../dataset/Dataset/Dataset/dataset_2.1_rl
```

### Endpoints

- `POST /knowledge-base/ingest` — `{ folder_path, preview?, limit? }`
- `GET  /knowledge-base/stats` — counts by category/severity/reviewer, distinct files
- `GET  /knowledge-base/projection?method=umap&refresh=false` — 2-D embedding map

### Visualization

The `/knowledge-base` page plots precedents, rules and source passages in a
shared 2-D space (single UMAP fit, PCA fallback), colored by index with
severity/category filters.

### Evaluation

`python -m scripts.eval_precedent_replay --folder <dataset_2.1_rl> --eval-frac 0.1`
runs a leakage-safe replay (train/eval split by file hash; same-document
precedents excluded) and writes precision/recall, missed-criticals and
comment-cosine metrics to `logs/eval_replay.json`.

### Operational note

If the knowledge base is empty, analysis produces **no** violations and the run
metadata carries `degraded: "knowledge_base_empty"` with a prominent log warning
— ingest the corpus to enable grading.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add Vector Memory System section to README"
```

---

### Task 15: Full regression + ingestion smoke test

**Files:** none (verification only). Follow superpowers:verification-before-completion — run every command and confirm output before claiming success.

- [ ] **Step 1: Bring the stack up and migrate**

Run:
```bash
docker compose up -d --build
docker exec compliance-backend alembic upgrade head
docker exec compliance-postgres psql -U compliance_user -d compliance_db -c "\dt rag_compliance_examples"
```
Expected: migration reports head `0004`; the table is listed.

- [ ] **Step 2: Run the full backend unit suite**

Run: `docker exec compliance-backend python -m pytest -q` (or `cd backend && python -m pytest -q`)
Expected: all tests pass, including the new ones from Tasks 1, 2, 3, 5, 6, 8, 9, 10, 11, 12 and the pre-existing `tests/test_imports.py`, `tests/services/rag/stores/*`.

- [ ] **Step 3: Hit every existing endpoint; confirm 200 + unchanged shapes**

Run (adjust host as needed):
```bash
curl -s localhost:8000/health
curl -s localhost:8000/health/rag
curl -s localhost:8000/submissions
curl -s localhost:8000/rules
curl -s localhost:8000/dashboard/summary
```
Expected: each returns HTTP 200 with its existing JSON shape (no schema changes).

- [ ] **Step 4: Ingest a sample and confirm stats**

Run:
```bash
docker exec compliance-backend python -m scripts.ingest_knowledge_base --limit 50
curl -s localhost:8000/knowledge-base/stats
```
Expected: ingestion summary shows `inserted > 0`; `/stats` `total > 0` with non-empty `by_severity` (keys among critical/moderate/informational) and `by_violation_category`.

- [ ] **Step 5: Run one submission end-to-end through the precedent path**

Create a submission (UI `/new` or `POST /submissions`), trigger analysis (`POST /compliance/analyze/{id}/sync`), then `GET /compliance/results/{id}`.
Expected: violations carry `current_text`, new-vocab `category`/`severity`, and a computed score. Confirm `ComplianceResults` shape matches `frontend/lib/types.ts` (violations/scores/grade).

- [ ] **Step 6: Confirm the empty-KB degraded signal**

On a database where `rag_compliance_examples` is empty (or before Step 4), run an analysis and inspect the run metadata / logs.
Expected: `metadata.degraded == "knowledge_base_empty"` and a prominent warning in the backend logs; zero violations produced.

- [ ] **Step 7: Projection endpoint + frontend page**

Run: `curl -s "localhost:8000/knowledge-base/projection?method=umap" | head -c 400`
Then open `http://localhost:3000/knowledge-base` (or `:3001` per the local override).
Expected: endpoint returns `{ method, computed_at, counts, points: [...] }` with points; the page renders the scatter with the new Sidebar entry; existing pages unchanged.

- [ ] **Step 8: One eval replay run**

Run: `docker exec compliance-backend python -m scripts.eval_precedent_replay --folder /app/dataset/Dataset/Dataset/dataset_2.1_rl --eval-frac 0.1`
Expected: completes and writes `logs/eval_replay.json` with precision/recall, missed_criticals, mean_comment_cosine, and counts. (This is a quality signal, not a pass/fail gate.)

- [ ] **Step 9: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: precedent engine regression + ingestion smoke verified"
```

---

## Self-Review (performed during planning)

**Spec coverage** — each §19 step maps to a task: 1→T1, 2→T2, 3→T3, 4→T4, 5→T5, 6→T6, 7→T7, 8→T8, 9→T9, 10→T10, 11→T11, 12→T12, 13→T13, 14→T14, 15→T15. §5 migration → T1; §6 store changes → T1; §7 ingestion → T3/T4; §8 dispatch → T7; §9 analysis/prompt/temp → T8; §10 validation → T9; §11 weights → T10; §12 API → T5; §12.5 viz → T12/T13; §13 config/deps → T2; §14 README → T14; §16 degradations honored (no-fallback, empty-KB guard, critic bypass on precedent path, new-vocab severities); §17 verification → T15; §17.5 eval → T11.

**Placeholder scan** — every code step contains complete, runnable code; no "TBD"/"add error handling"/"similar to". Test code is spelled out per task.

**Type/name consistency** — `IndexName` value `rag_compliance_examples` is used identically in ports, store dicts, indexer, retriever, projection. `retrieved_examples` matches between `state.py`, `dispatch_node` return, and `analysis_node` read. `validate_agent_output(output) -> (bool, list)` signature matches its test and its callers in `analysis_node`/eval. `create_precedent_prompts(content, precedents)` matches test + node call. `get_precedent_retriever()` / `retrieve_per_chunk(chunks, top_k, exclude_document_id)` consistent across node + eval. `SEVERITY_WEIGHTS` keys `moderate`/`informational` match the validator's allowed set. `temperature` param default `0.2` preserves all existing `generate_structured_response` callers; only the precedent path passes `0.0`.

**Known intentional deviations from the literal spec** (all documented above): target the `_rl` corpus; anchor-ellipsis stripping; additive `temperature` param; additive read-only Docker mount; frontend verified via typecheck/build (no test runner); sync unit tests only.
