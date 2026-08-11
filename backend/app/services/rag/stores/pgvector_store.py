"""pgvector + Postgres tsvector vector store (v1 default backend).

Speaks the VectorStore protocol over the existing Postgres connection.
- Upserts handled via INSERT ... ON CONFLICT DO UPDATE.
- Hybrid search: parallel vector + BM25 queries, fused in Python via RRF.
- Filters: dict of field -> value or list; serialized to safe parameterized SQL.

The store is sync internally but presents an async surface so it can be
called from async retrievers / indexers without thread-pool ceremony at
the call site. Heavy queries are wrapped with run_in_executor.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.ports import IndexName, SearchHit, VectorDoc
from app.services.rag.rrf import reciprocal_rank_fusion
from app.services.rag import trace as rag_trace

logger = logging.getLogger(__name__)


# Field whitelist per index — protects against SQL injection in `filters` keys.
# Values are still passed via parameter binding.
# product_category / product_line enable pushdown scoping: retrieve top-K
# WITHIN the resolved product scope instead of retrieving globally and
# discarding the out-of-scope survivors in Python (applicability.validate_*).
# Pass None in the collection to keep untagged rows, per the C2/C7 global
# contract — the applicability judge, not the SQL, is the strict gate.
# `product_line` exists on all four rag_* corpora since migration 0036.
_FILTER_WHITELIST: Dict[IndexName, set] = {
    "rag_rules": {"category", "severity", "is_active", "product_line"},
    "rag_chunks": {
        "submission_id", "submission_status", "chunk_index", "product_line",
    },
    "rag_source_docs": {"document_id", "regulator", "product_line"},
    "rag_compliance_examples": {
        "reviewer_name", "violation_category", "severity", "document_id",
    },
    "rag_product_docs": {
        "product_document_id", "uin", "product_name", "block_type", "product_line",
    },
    "precedent_cases": {
        "issue_type", "severity", "ticket", "is_reviewer", "guideline_ref",
        "product_category",
    },
}

# Per-index column lists for return shape.
_RETURN_COLUMNS: Dict[IndexName, List[str]] = {
    "rag_rules": [
        "id", "category", "severity", "is_active", "rule_text",
        "keywords", "source",
    ],
    "rag_chunks": [
        "id", "submission_id", "chunk_index", "page_number", "text",
        "submission_status", "submission_summary",
    ],
    "rag_source_docs": [
        "id", "document_id", "document_title", "regulator", "chunk_index",
        "page_number", "text", "derived_rule_ids",
    ],
    "rag_compliance_examples": [
        "id", "document_id", "title", "task", "section_label", "chunk_text",
        "anchor_text", "reviewer_name", "comment_text", "final_text_chunk",
        "violation_category", "severity", "source_file",
    ],
    "rag_product_docs": [
        "id", "product_document_id", "uin", "product_name", "chunk_index",
        "page_number", "section_path", "block_type", "text",
    ],
    "precedent_cases": [
        "id", "canonical_hash", "highlighted_span", "span_context",
        "reviewer_comment", "reviewer_role", "is_reviewer", "thread", "resolved",
        "before_text", "after_text", "regulation_tags", "issue_type",
        "why_rationale", "guideline_ref", "severity", "product_category",
        "ticket", "source_file", "comment_date", "occurrence_count",
        "example_tickets", "source_layer_id",
    ],
}


# Corpus layers (migration 0030). A precedent belonging to a DISABLED layer is
# invisible to retrieval — instantly, reversibly, and without re-embedding
# anything. This lives in the store, not in the callers, because a caller that
# forgets the filter silently resurrects content an admin switched off.
#
# NULL-safe by construction: `source_layer_id IS NULL` is checked FIRST, so every
# precedent that predates layers (all ~2,440 production rows) stays retrievable.
# The NOT IN subquery selects a PK, which can never be NULL, so the usual
# NOT-IN/NULL trap does not apply either.
_LAYER_GUARD: Dict[IndexName, str] = {
    "precedent_cases": (
        " AND (source_layer_id IS NULL OR source_layer_id NOT IN"
        " (SELECT id FROM corpus_layers WHERE enabled = FALSE))"
    ),
}


def _vec_literal(vec: List[float]) -> str:
    """Format a float list as a pgvector literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _uuid_array_literal(ids) -> str:
    """Build a Postgres UUID[] literal '{u1,u2}' from `ids`, validating each
    element as a real UUID first. Even though the literal is passed as a bound
    parameter, its CONTENTS are concatenated — a malformed/injected id could
    otherwise corrupt the array literal. Raises ValueError on any bad id.
    See architect-audit H17."""
    import uuid as _uuid

    validated = [str(_uuid.UUID(str(x))) for x in ids]
    return "{" + ",".join(validated) + "}"


def _build_filter_clause(
    index: IndexName, filters: Optional[Dict[str, Any]], params: Dict[str, Any]
) -> str:
    """Build a parameterized 'AND ...' clause. Mutates `params`.

    NULL semantics matter here. SQL's `IN` never matches NULL, so a scoping
    filter like product_category IN ('ulip','par') silently EXCLUDES every
    untagged row — the opposite of the applicability contract, where an
    untagged item is GLOBAL and must still be retrieved (applicability.py,
    C2/C7). Pass None inside the collection to mean "...or untagged":

        {"product_category": ["ulip", "par", None]}
            -> (product_category IN (:a,:b) OR product_category IS NULL)

    A bare None means "untagged only" and emits IS NULL — not `= NULL`,
    which is never true for any row.
    """
    if not filters:
        return ""
    allowed = _FILTER_WHITELIST[index]
    parts: List[str] = []
    for k, v in filters.items():
        if k not in allowed:
            raise ValueError(f"Filter field '{k}' not allowed for {index}")
        if isinstance(v, (list, tuple, set)):
            if not v:
                parts.append("FALSE")
                continue
            values = [item for item in v if item is not None]
            include_null = len(values) != len(v)
            placeholders = []
            for i, item in enumerate(values):
                p = f"f_{k}_{i}"
                placeholders.append(f":{p}")
                params[p] = item
            if not placeholders:          # e.g. [None] -> untagged only
                parts.append(f"{k} IS NULL")
            elif include_null:
                parts.append(f"({k} IN ({','.join(placeholders)}) OR {k} IS NULL)")
            else:
                parts.append(f"{k} IN ({','.join(placeholders)})")
        elif v is None:
            parts.append(f"{k} IS NULL")
        else:
            p = f"f_{k}"
            params[p] = v
            parts.append(f"{k} = :{p}")
    return " AND " + " AND ".join(parts) if parts else ""


# ------------------------------------------------------------- upsert ---

_UPSERT_SQL: Dict[IndexName, str] = {
    "rag_rules": """
        INSERT INTO rag_rules (id, category, severity, is_active, rule_text,
                               keywords, embed_text, embedding, source,
                               product_line,
                               embedding_model, embedding_dim, updated_at)
        VALUES (:id, :category, :severity, :is_active, :rule_text,
                CAST(:keywords AS JSONB), :embed_text, CAST(:embedding AS VECTOR), :source,
                :product_line,
                :embedding_model, :embedding_dim, NOW())
        ON CONFLICT (id) DO UPDATE SET
          product_line = EXCLUDED.product_line,
          category = EXCLUDED.category,
          severity = EXCLUDED.severity,
          is_active = EXCLUDED.is_active,
          rule_text = EXCLUDED.rule_text,
          keywords = EXCLUDED.keywords,
          embed_text = EXCLUDED.embed_text,
          embedding = EXCLUDED.embedding,
          source = EXCLUDED.source,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim,
          updated_at = NOW()
    """,
    "rag_chunks": """
        INSERT INTO rag_chunks (id, submission_id, chunk_index, page_number, text,
                                embedding, submission_status, submission_summary,
                                product_line,
                                embedding_model, embedding_dim, updated_at)
        VALUES (:id, :submission_id, :chunk_index, :page_number, :text,
                CAST(:embedding AS VECTOR), :submission_status, :submission_summary,
                :product_line,
                :embedding_model, :embedding_dim, NOW())
        ON CONFLICT (id) DO UPDATE SET
          product_line = EXCLUDED.product_line,
          submission_id = EXCLUDED.submission_id,
          chunk_index = EXCLUDED.chunk_index,
          page_number = EXCLUDED.page_number,
          text = EXCLUDED.text,
          embedding = EXCLUDED.embedding,
          submission_status = EXCLUDED.submission_status,
          submission_summary = EXCLUDED.submission_summary,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim,
          updated_at = NOW()
    """,
    "rag_source_docs": """
        INSERT INTO rag_source_docs (id, document_id, document_title, regulator,
                                     chunk_index, page_number, text, embedding,
                                     derived_rule_ids, product_line,
                                     embedding_model, embedding_dim,
                                     uploaded_at)
        VALUES (:id, :document_id, :document_title, :regulator,
                :chunk_index, :page_number, :text, CAST(:embedding AS VECTOR),
                CAST(:derived_rule_ids AS UUID[]), :product_line,
                :embedding_model, :embedding_dim, NOW())
        ON CONFLICT (id) DO UPDATE SET
          product_line = EXCLUDED.product_line,
          document_id = EXCLUDED.document_id,
          document_title = EXCLUDED.document_title,
          regulator = EXCLUDED.regulator,
          chunk_index = EXCLUDED.chunk_index,
          page_number = EXCLUDED.page_number,
          text = EXCLUDED.text,
          embedding = EXCLUDED.embedding,
          derived_rule_ids = EXCLUDED.derived_rule_ids,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim
    """,
    "rag_compliance_examples": """
        INSERT INTO rag_compliance_examples (
            id, document_id, title, task, section_label, chunk_text, anchor_text,
            reviewer_name, comment_text, final_text_chunk, violation_category,
            severity, source_file, embed_text, embedding, embedding_model, embedding_dim)
        VALUES (
            :id, :document_id, :title, :task, :section_label, :chunk_text, :anchor_text,
            :reviewer_name, :comment_text, :final_text_chunk, :violation_category,
            :severity, :source_file, :embed_text, CAST(:embedding AS VECTOR),
            :embedding_model, :embedding_dim)
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
          embedding = EXCLUDED.embedding,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim
    """,
    "rag_product_docs": """
        INSERT INTO rag_product_docs (id, product_document_id, uin, product_name,
                                      chunk_index, page_number, section_path,
                                      block_type, text, embedding, product_line,
                                      embedding_model, embedding_dim, updated_at)
        VALUES (:id, :product_document_id, :uin, :product_name,
                :chunk_index, :page_number, :section_path,
                :block_type, :text, CAST(:embedding AS VECTOR), :product_line,
                :embedding_model, :embedding_dim, NOW())
        ON CONFLICT (id) DO UPDATE SET
          product_line = EXCLUDED.product_line,
          product_document_id = EXCLUDED.product_document_id,
          uin = EXCLUDED.uin,
          product_name = EXCLUDED.product_name,
          chunk_index = EXCLUDED.chunk_index,
          page_number = EXCLUDED.page_number,
          section_path = EXCLUDED.section_path,
          block_type = EXCLUDED.block_type,
          text = EXCLUDED.text,
          embedding = EXCLUDED.embedding,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim,
          updated_at = NOW()
    """,
    "precedent_cases": """
        INSERT INTO precedent_cases (
            id, canonical_hash, highlighted_span, span_context, reviewer_comment,
            reviewer_role, is_reviewer, thread, resolved, before_text, after_text,
            regulation_tags, issue_type, why_rationale, guideline_ref, severity,
            product_category, ticket, source_file, comment_date, occurrence_count,
            example_tickets, embed_text, embedding, embedding_model, embedding_dim, updated_at)
        VALUES (
            :id, :canonical_hash, :highlighted_span, :span_context, :reviewer_comment,
            :reviewer_role, :is_reviewer, CAST(:thread AS JSONB), :resolved, :before_text, :after_text,
            CAST(:regulation_tags AS JSONB), :issue_type, :why_rationale, :guideline_ref, :severity,
            :product_category, :ticket, :source_file, CAST(:comment_date AS TIMESTAMPTZ), :occurrence_count,
            CAST(:example_tickets AS JSONB), :embed_text, CAST(:embedding AS VECTOR),
            :embedding_model, :embedding_dim, NOW())
        ON CONFLICT (id) DO UPDATE SET
          canonical_hash = EXCLUDED.canonical_hash,
          highlighted_span = EXCLUDED.highlighted_span,
          span_context = EXCLUDED.span_context,
          reviewer_comment = EXCLUDED.reviewer_comment,
          reviewer_role = EXCLUDED.reviewer_role,
          is_reviewer = EXCLUDED.is_reviewer,
          thread = EXCLUDED.thread,
          resolved = EXCLUDED.resolved,
          before_text = EXCLUDED.before_text,
          after_text = EXCLUDED.after_text,
          regulation_tags = EXCLUDED.regulation_tags,
          issue_type = EXCLUDED.issue_type,
          why_rationale = EXCLUDED.why_rationale,
          guideline_ref = EXCLUDED.guideline_ref,
          severity = EXCLUDED.severity,
          product_category = EXCLUDED.product_category,
          ticket = EXCLUDED.ticket,
          source_file = EXCLUDED.source_file,
          comment_date = EXCLUDED.comment_date,
          occurrence_count = EXCLUDED.occurrence_count,
          example_tickets = EXCLUDED.example_tickets,
          embed_text = EXCLUDED.embed_text,
          embedding = EXCLUDED.embedding,
          embedding_model = EXCLUDED.embedding_model,
          embedding_dim = EXCLUDED.embedding_dim,
          updated_at = NOW()
    """,
}


def _active_embedder_identity() -> Tuple[Optional[str], Optional[int]]:
    """(model, dim) of the currently-configured embedder, for stamping vectors.

    Best-effort: if the embedder can't be constructed we stamp NULLs rather than
    blocking the upsert (the query-time assert still protects retrieval)."""
    try:
        from app.services.rag.factory import get_embedder
        emb = get_embedder()
        return getattr(emb, "model", None), getattr(emb, "dim", None)
    except Exception:  # pragma: no cover
        return None, None


def _upsert_params(index: IndexName, doc: VectorDoc) -> Dict[str, Any]:
    f = doc.fields
    model, dim = _active_embedder_identity()
    base = {
        "id": doc.id,
        "embedding": _vec_literal(doc.embedding),
        "embedding_model": model,
        "embedding_dim": dim,
    }
    # Product scope on the four rag_* corpora (0036). Absent -> NULL, which
    # stays retrievable under every scope (the filter always admits NULL) and
    # visibly unscoped for curation — never guessed into a family here.
    scoped = {**base, "product_line": f.get("product_line")}
    if index == "rag_rules":
        return {
            **scoped,
            "category": f.get("category", ""),
            "severity": f.get("severity", "medium"),
            "is_active": bool(f.get("is_active", True)),
            "rule_text": f.get("rule_text", ""),
            "keywords": json.dumps(f.get("keywords") or []),
            "embed_text": f.get("embed_text", ""),
            "source": f.get("source"),
        }
    if index == "rag_chunks":
        return {
            **scoped,
            "submission_id": f["submission_id"],
            "chunk_index": int(f.get("chunk_index", 0)),
            "page_number": f.get("page_number"),
            "text": f.get("text", ""),
            "submission_status": f.get("submission_status", "analyzing"),
            "submission_summary": f.get("submission_summary"),
        }
    if index == "rag_source_docs":
        derived = f.get("derived_rule_ids") or []
        # Postgres UUID[] literal: '{uuid1,uuid2}' (each element validated)
        derived_lit = _uuid_array_literal(derived)
        return {
            **scoped,
            "document_id": f["document_id"],
            "document_title": f.get("document_title", ""),
            "regulator": f.get("regulator", ""),
            "chunk_index": int(f.get("chunk_index", 0)),
            "page_number": f.get("page_number"),
            "text": f.get("text", ""),
            "derived_rule_ids": derived_lit,
        }
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
    if index == "rag_product_docs":
        return {
            **scoped,
            "product_document_id": f["product_document_id"],
            "uin": f.get("uin"),
            "product_name": f.get("product_name", ""),
            "chunk_index": int(f.get("chunk_index", 0)),
            "page_number": f.get("page_number"),
            "section_path": f.get("section_path"),
            "block_type": f.get("block_type", "prose"),
            "text": f.get("text", ""),
        }
    if index == "precedent_cases":
        return {
            **base,
            "canonical_hash": f["canonical_hash"],
            "highlighted_span": f.get("highlighted_span", ""),
            "span_context": f.get("span_context"),
            "reviewer_comment": f.get("reviewer_comment", ""),
            "reviewer_role": f.get("reviewer_role"),
            "is_reviewer": bool(f.get("is_reviewer", False)),
            "thread": json.dumps(f.get("thread") or []),
            "resolved": bool(f.get("resolved", False)),
            "before_text": f.get("before_text"),
            "after_text": f.get("after_text"),
            "regulation_tags": json.dumps(f.get("regulation_tags") or []),
            "issue_type": f.get("issue_type"),
            "why_rationale": f.get("why_rationale"),
            "guideline_ref": f.get("guideline_ref"),
            "severity": f.get("severity", "informational"),
            "product_category": f.get("product_category"),
            "ticket": f.get("ticket"),
            "source_file": f.get("source_file", ""),
            "comment_date": f.get("comment_date"),
            "occurrence_count": int(f.get("occurrence_count", 1)),
            "example_tickets": json.dumps(f.get("example_tickets") or []),
            "embed_text": f.get("embed_text", ""),
        }
    raise ValueError(f"Unknown index: {index}")


# ------------------------------------------------------------- search ---

def _vector_leg_sql(index: IndexName, filter_clause: str) -> str:
    # Cosine floor (:min_cosine) drops semantically-unrelated candidates before
    # fusion so retrieval doesn't always return K rows for an irrelevant query
    # (hallucinated grounding — audit C6). Floor is on cosine [-1,1], not RRF.
    return f"""
        SELECT id, 1 - (embedding <=> CAST(:qvec AS VECTOR)) AS score
        FROM {index}
        WHERE TRUE {filter_clause}
          AND 1 - (embedding <=> CAST(:qvec AS VECTOR)) >= :min_cosine
        ORDER BY embedding <=> CAST(:qvec AS VECTOR), id
        LIMIT :recall
    """


def _keyword_leg_sql(index: IndexName, filter_clause: str) -> str:
    # Relevance floor (:min_ts_rank) on BM25, mirroring the cosine floor on the
    # vector leg. Drops rows that only match a single common token so an
    # off-topic precedent/rule can't ride a lexical coincidence into RRF and be
    # cited. See architect-audit (BM25 leg had no floor).
    return f"""
        SELECT id, ts_rank_cd(search_tsv, plainto_tsquery('english', :qtext)) AS score
        FROM {index}
        WHERE search_tsv @@ plainto_tsquery('english', :qtext) {filter_clause}
          AND ts_rank_cd(search_tsv, plainto_tsquery('english', :qtext)) >= :min_ts_rank
        ORDER BY score DESC, id
        LIMIT :recall
    """


def _fetch_sql(index: IndexName) -> str:
    cols = _RETURN_COLUMNS[index]
    return f"SELECT {', '.join(cols)} FROM {index} WHERE id = ANY(CAST(:ids AS UUID[]))"


# Indexes whose stored embedding model has already been validated against the
# active embedder this process — so the check is one query per index, not per call.
_embedding_checked: set = set()


def _assert_embedding_compat(db: Session, index: IndexName) -> None:
    """Fail closed if rows in ``index`` were embedded with a DIFFERENT model than
    the one now answering queries. Cosine similarity across embedding spaces is
    meaningless but returns plausible scores → silent retrieval corruption.

    Legacy rows with NULL embedding_model (pre-0009 corpora) are tolerated — we
    can't know their model, so we can't assert; new upserts stamp it. Only a
    CONCRETE mismatch raises. Runs once per index per process.
    """
    if index in _embedding_checked:
        return
    active_model, _ = _active_embedder_identity()
    if not active_model:
        _embedding_checked.add(index)
        return
    rows = db.execute(
        text(
            f"SELECT DISTINCT embedding_model FROM {index} "
            f"WHERE embedding_model IS NOT NULL"
        )
    ).all()
    stored = {str(r[0]) for r in rows}
    mismatched = stored - {active_model}
    if mismatched:
        raise RAGDegraded(
            f"Embedding-model mismatch on {index}: active embedder is "
            f"'{active_model}' but rows were embedded with {sorted(mismatched)}. "
            f"Re-index {index} under the active model or restore the prior "
            f"RAG_EMBEDDING_PROVIDER/MODEL. Refusing to serve corrupt retrieval."
        )
    _embedding_checked.add(index)


def reset_embedding_check_cache() -> None:
    """Test/ops helper — clears the per-process validation cache."""
    _embedding_checked.clear()


# =========================================================== PgVectorStore

class PgVectorStore:
    """VectorStore implementation backed by Postgres + pgvector + tsvector."""

    name = "pgvector"

    # ------------------ session helpers ------------------

    def _session(self) -> Session:
        return SessionLocal()

    async def _run_sync(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ------------------ upsert / delete ------------------

    async def upsert(self, index: IndexName, docs: List[VectorDoc]) -> None:
        if not docs:
            return
        sql = _UPSERT_SQL[index]
        rows = [_upsert_params(index, d) for d in docs]

        def _do():
            db = self._session()
            try:
                db.execute(text(sql), rows)
                db.commit()
            except Exception as e:
                db.rollback()
                raise RAGIndexingFailed(f"pgvector upsert into {index} failed: {e}") from e
            finally:
                db.close()

        await self._run_sync(_do)

    async def delete(self, index: IndexName, ids: List[str]) -> None:
        if not ids:
            return

        def _do():
            db = self._session()
            try:
                db.execute(
                    text(f"DELETE FROM {index} WHERE id = ANY(CAST(:ids AS UUID[]))"),
                    {"ids": _uuid_array_literal(ids)},
                )
                db.commit()
            except Exception as e:
                db.rollback()
                raise RAGIndexingFailed(f"pgvector delete from {index} failed: {e}") from e
            finally:
                db.close()

        await self._run_sync(_do)

    # ------------------ search ------------------

    async def hybrid_search(
        self,
        index: IndexName,
        query_text: str,
        query_vector: List[float],
        top_k: int,
        recall_pool: int,
        rrf_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        # Per-leg scores/ranks otherwise die inside _do(). Resolved HERE, in the
        # async body: run_in_executor does not copy the context, so a contextvar
        # read from the worker thread would always miss. None => nobody is
        # collecting and the trace costs one dict lookup.
        tracer = rag_trace.begin_query(index=index, top_k=top_k, filters=filters)

        def _do() -> List[SearchHit]:
            db = self._session()
            try:
                # Fail closed if the corpus was embedded with a different model
                # than the one now querying (silent retrieval corruption).
                _assert_embedding_compat(db, index)
                params: Dict[str, Any] = {
                    "qvec": _vec_literal(query_vector),
                    "qtext": query_text or "",
                    "recall": recall_pool,
                    "min_cosine": settings.rag_min_cosine,
                    "min_ts_rank": settings.rag_min_ts_rank,
                }
                # Caller filters, then the non-negotiable disabled-layer guard.
                fclause = _build_filter_clause(index, filters, params) + _LAYER_GUARD.get(index, "")

                # Vector leg
                vec_rows = db.execute(text(_vector_leg_sql(index, fclause)), params).all()
                vec_ranked: List[Tuple[str, float]] = [(str(r[0]), float(r[1])) for r in vec_rows]

                # Keyword leg (skip if no query text)
                kw_ranked: List[Tuple[str, float]] = []
                if query_text and query_text.strip():
                    kw_rows = db.execute(text(_keyword_leg_sql(index, fclause)), params).all()
                    kw_ranked = [(str(r[0]), float(r[1])) for r in kw_rows]

                # Fuse. The FULL fused list is traced before the cut — the rows
                # just below top_k are exactly the "it was retrieved, it just
                # ranked out" answer the inspector needs.
                fused_all = reciprocal_rank_fusion([vec_ranked, kw_ranked], k=rrf_k)
                if tracer is not None:
                    tracer.record(vec_ranked, kw_ranked, fused_all)
                fused = fused_all[:top_k]
                if not fused:
                    return []

                # Hydrate full rows
                ids = [doc_id for doc_id, _ in fused]
                rows = db.execute(
                    text(_fetch_sql(index)),
                    {"ids": _uuid_array_literal(ids)},
                ).all()
                cols = _RETURN_COLUMNS[index]
                by_id = {str(r[0]): {c: r[i] for i, c in enumerate(cols)} for r in rows}

                hits: List[SearchHit] = []
                for doc_id, score in fused:
                    fields = by_id.get(doc_id)
                    if fields is None:
                        continue
                    # Cast UUIDs/lists to JSON-safe types
                    fields = {
                        k: (str(v) if hasattr(v, "hex") else v) for k, v in fields.items()
                    }
                    hits.append(SearchHit(id=doc_id, score=score, fields=fields))
                return hits
            except Exception as e:
                logger.error(f"pgvector hybrid_search on {index} failed: {e}")
                raise RAGDegraded(str(e)) from e
            finally:
                db.close()

        return await self._run_sync(_do)

    async def vector_search(
        self,
        index: IndexName,
        query_vector: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchHit]:
        # Reuse hybrid path with empty query_text so the keyword leg is skipped.
        return await self.hybrid_search(
            index=index,
            query_text="",
            query_vector=query_vector,
            top_k=top_k,
            recall_pool=top_k,
            rrf_k=60,
            filters=filters,
        )

    async def health(self) -> bool:
        def _do() -> bool:
            db = self._session()
            try:
                db.execute(text("SELECT 1"))
                return True
            except Exception:
                return False
            finally:
                db.close()

        return await self._run_sync(_do)
