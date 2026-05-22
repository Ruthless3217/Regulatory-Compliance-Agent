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

from app.database import SessionLocal
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.ports import IndexName, SearchHit, VectorDoc
from app.services.rag.rrf import reciprocal_rank_fusion

logger = logging.getLogger(__name__)


# Field whitelist per index — protects against SQL injection in `filters` keys.
# Values are still passed via parameter binding.
_FILTER_WHITELIST: Dict[IndexName, set] = {
    "rag_rules": {"category", "severity", "is_active"},
    "rag_chunks": {"submission_id", "submission_status", "chunk_index"},
    "rag_source_docs": {"document_id", "regulator"},
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
}


def _vec_literal(vec: List[float]) -> str:
    """Format a float list as a pgvector literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _build_filter_clause(
    index: IndexName, filters: Optional[Dict[str, Any]], params: Dict[str, Any]
) -> str:
    """Build a parameterized 'AND ...' clause. Mutates `params`."""
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
            placeholders = []
            for i, item in enumerate(v):
                p = f"f_{k}_{i}"
                placeholders.append(f":{p}")
                params[p] = item
            parts.append(f"{k} IN ({','.join(placeholders)})")
        else:
            p = f"f_{k}"
            params[p] = v
            parts.append(f"{k} = :{p}")
    return " AND " + " AND ".join(parts) if parts else ""


# ------------------------------------------------------------- upsert ---

_UPSERT_SQL: Dict[IndexName, str] = {
    "rag_rules": """
        INSERT INTO rag_rules (id, category, severity, is_active, rule_text,
                               keywords, embed_text, embedding, source, updated_at)
        VALUES (:id, :category, :severity, :is_active, :rule_text,
                CAST(:keywords AS JSONB), :embed_text, CAST(:embedding AS VECTOR), :source, NOW())
        ON CONFLICT (id) DO UPDATE SET
          category = EXCLUDED.category,
          severity = EXCLUDED.severity,
          is_active = EXCLUDED.is_active,
          rule_text = EXCLUDED.rule_text,
          keywords = EXCLUDED.keywords,
          embed_text = EXCLUDED.embed_text,
          embedding = EXCLUDED.embedding,
          source = EXCLUDED.source,
          updated_at = NOW()
    """,
    "rag_chunks": """
        INSERT INTO rag_chunks (id, submission_id, chunk_index, page_number, text,
                                embedding, submission_status, submission_summary, updated_at)
        VALUES (:id, :submission_id, :chunk_index, :page_number, :text,
                CAST(:embedding AS VECTOR), :submission_status, :submission_summary, NOW())
        ON CONFLICT (id) DO UPDATE SET
          submission_id = EXCLUDED.submission_id,
          chunk_index = EXCLUDED.chunk_index,
          page_number = EXCLUDED.page_number,
          text = EXCLUDED.text,
          embedding = EXCLUDED.embedding,
          submission_status = EXCLUDED.submission_status,
          submission_summary = EXCLUDED.submission_summary,
          updated_at = NOW()
    """,
    "rag_source_docs": """
        INSERT INTO rag_source_docs (id, document_id, document_title, regulator,
                                     chunk_index, page_number, text, embedding,
                                     derived_rule_ids, uploaded_at)
        VALUES (:id, :document_id, :document_title, :regulator,
                :chunk_index, :page_number, :text, CAST(:embedding AS VECTOR),
                CAST(:derived_rule_ids AS UUID[]), NOW())
        ON CONFLICT (id) DO UPDATE SET
          document_id = EXCLUDED.document_id,
          document_title = EXCLUDED.document_title,
          regulator = EXCLUDED.regulator,
          chunk_index = EXCLUDED.chunk_index,
          page_number = EXCLUDED.page_number,
          text = EXCLUDED.text,
          embedding = EXCLUDED.embedding,
          derived_rule_ids = EXCLUDED.derived_rule_ids
    """,
}


def _upsert_params(index: IndexName, doc: VectorDoc) -> Dict[str, Any]:
    f = doc.fields
    base = {"id": doc.id, "embedding": _vec_literal(doc.embedding)}
    if index == "rag_rules":
        return {
            **base,
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
            **base,
            "submission_id": f["submission_id"],
            "chunk_index": int(f.get("chunk_index", 0)),
            "page_number": f.get("page_number"),
            "text": f.get("text", ""),
            "submission_status": f.get("submission_status", "analyzing"),
            "submission_summary": f.get("submission_summary"),
        }
    if index == "rag_source_docs":
        derived = f.get("derived_rule_ids") or []
        # Postgres UUID[] literal: '{uuid1,uuid2}'
        derived_lit = "{" + ",".join(str(x) for x in derived) + "}"
        return {
            **base,
            "document_id": f["document_id"],
            "document_title": f.get("document_title", ""),
            "regulator": f.get("regulator", ""),
            "chunk_index": int(f.get("chunk_index", 0)),
            "page_number": f.get("page_number"),
            "text": f.get("text", ""),
            "derived_rule_ids": derived_lit,
        }
    raise ValueError(f"Unknown index: {index}")


# ------------------------------------------------------------- search ---

def _vector_leg_sql(index: IndexName, filter_clause: str) -> str:
    return f"""
        SELECT id, 1 - (embedding <=> CAST(:qvec AS VECTOR)) AS score
        FROM {index}
        WHERE TRUE {filter_clause}
        ORDER BY embedding <=> CAST(:qvec AS VECTOR)
        LIMIT :recall
    """


def _keyword_leg_sql(index: IndexName, filter_clause: str) -> str:
    return f"""
        SELECT id, ts_rank_cd(search_tsv, plainto_tsquery('english', :qtext)) AS score
        FROM {index}
        WHERE search_tsv @@ plainto_tsquery('english', :qtext) {filter_clause}
        ORDER BY score DESC
        LIMIT :recall
    """


def _fetch_sql(index: IndexName) -> str:
    cols = _RETURN_COLUMNS[index]
    return f"SELECT {', '.join(cols)} FROM {index} WHERE id = ANY(CAST(:ids AS UUID[]))"


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
                    {"ids": "{" + ",".join(ids) + "}"},
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
        def _do() -> List[SearchHit]:
            db = self._session()
            try:
                params: Dict[str, Any] = {
                    "qvec": _vec_literal(query_vector),
                    "qtext": query_text or "",
                    "recall": recall_pool,
                }
                fclause = _build_filter_clause(index, filters, params)

                # Vector leg
                vec_rows = db.execute(text(_vector_leg_sql(index, fclause)), params).all()
                vec_ranked: List[Tuple[str, float]] = [(str(r[0]), float(r[1])) for r in vec_rows]

                # Keyword leg (skip if no query text)
                kw_ranked: List[Tuple[str, float]] = []
                if query_text and query_text.strip():
                    kw_rows = db.execute(text(_keyword_leg_sql(index, fclause)), params).all()
                    kw_ranked = [(str(r[0]), float(r[1])) for r in kw_rows]

                # Fuse
                fused = reciprocal_rank_fusion([vec_ranked, kw_ranked], k=rrf_k)[:top_k]
                if not fused:
                    return []

                # Hydrate full rows
                ids = [doc_id for doc_id, _ in fused]
                rows = db.execute(
                    text(_fetch_sql(index)),
                    {"ids": "{" + ",".join(ids) + "}"},
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
