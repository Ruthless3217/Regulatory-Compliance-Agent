"""Index staged regulator text and quote-level generated-rule evidence.

Coarse document passages remain unlinked. A generated draft receives a separate
row containing one exact source quote; approval publishes only that row after
passage ID, document ID, and quote text are re-verified.
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


_PASSAGE_TOKEN_CAP = 1200  # passages are coarser than analysis chunks


def _chunk_source_text(content: str) -> List[str]:
    """Reuse the project's token chunker if available; fall back to paragraphs."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(content)
        passages: List[str] = []
        start = 0
        while start < len(tokens):
            end = min(start + _PASSAGE_TOKEN_CAP, len(tokens))
            passages.append(enc.decode(tokens[start:end]))
            start = end
        return passages or [content]
    except ImportError:
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        return paras or [content]


async def index_source_document(
    document_id: uuid.UUID | str,
    document_title: str,
    regulator: str,
    full_text: str,
) -> List[Tuple[str, str]]:
    """Chunk + embed + upsert. Returns [(passage_id, passage_text), ...] in order."""
    if not full_text:
        return []
    passages = _chunk_source_text(full_text)
    embedder = get_embedder()
    store = get_vector_store()
    try:
        vectors = await embedder.embed(passages)
        docs: List[VectorDoc] = []
        result: List[Tuple[str, str]] = []
        for i, (passage, vec) in enumerate(zip(passages, vectors)):
            pid = str(uuid.uuid4())
            docs.append(
                VectorDoc(
                    id=pid,
                    embedding=vec,
                    fields={
                        "document_id": str(document_id),
                        "document_title": document_title,
                        "regulator": regulator,
                        "chunk_index": i,
                        "page_number": None,
                        "text": passage,
                        "derived_rule_ids": [],
                    },
                )
            )
            result.append((pid, passage))
        await store.upsert("rag_source_docs", docs)
        logger.info(
            f"Indexed source doc '{document_title}' ({regulator}): {len(docs)} passages"
        )
        return result
    except RAGDegraded as e:
        raise RAGIndexingFailed(str(e)) from e


async def index_source_evidence_quote(
    document_id: uuid.UUID | str,
    document_title: str,
    regulator: str,
    full_text: str,
    source_quote: str,
    evidence_index: int,
) -> str:
    """Stage one exact quote as the sole publishable evidence for a rule.

    The caller may not supply a paraphrase or text from another document. The
    row starts with no derived rule IDs and therefore remains unavailable to
    approved-source retrieval until the corresponding draft is activated.
    """
    quote = source_quote.strip() if isinstance(source_quote, str) else ""
    if not quote or quote not in full_text:
        raise RAGIndexingFailed(
            "source_quote is not an exact verbatim substring of the source document"
        )

    embedder = get_embedder()
    store = get_vector_store()
    try:
        vectors = await embedder.embed([quote])
        if len(vectors) != 1:
            raise RAGIndexingFailed("Evidence quote embedding returned no vector")
        passage_id = str(uuid.uuid4())
        await store.upsert(
            "rag_source_docs",
            [
                VectorDoc(
                    id=passage_id,
                    embedding=vectors[0],
                    fields={
                        "document_id": str(document_id),
                        "document_title": document_title,
                        "regulator": regulator,
                        "chunk_index": evidence_index,
                        "page_number": None,
                        "text": quote,
                        "derived_rule_ids": [],
                    },
                )
            ],
        )
        return passage_id
    except RAGDegraded as exc:
        raise RAGIndexingFailed(str(exc)) from exc


async def link_rules_to_passage(
    passage_id: uuid.UUID | str, rule_ids: List[uuid.UUID | str]
) -> None:
    """Backfill `derived_rule_ids` on a stored passage.

    pgvector path uses a raw UPDATE — no need to re-embed. Azure Search
    path goes through the store's upsert (merge_or_upload). We do the
    cheap pgvector path inline; for Azure we'd need a separate read-then-upsert
    (out of scope for v1 — Azure backend is enabled later).
    """
    if not rule_ids:
        return
    # pgvector fast path
    db: Session = SessionLocal()
    try:
        from app.services.rag.stores.pgvector_store import _uuid_array_literal
        arr_lit = _uuid_array_literal(rule_ids)
        db.execute(
            text(
                """
                UPDATE rag_source_docs
                SET derived_rule_ids = (
                  SELECT ARRAY(SELECT DISTINCT unnest(derived_rule_ids || CAST(:arr AS UUID[])))
                )
                WHERE id = CAST(:pid AS UUID)
                """
            ),
            {"arr": arr_lit, "pid": str(passage_id)},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"link_rules_to_passage failed: {e}")
        raise RAGIndexingFailed(str(e)) from e
    finally:
        db.close()


async def link_rule_to_source_quote(
    passage_id: uuid.UUID | str,
    document_id: uuid.UUID | str,
    source_quote: str,
    rule_id: uuid.UUID | str,
) -> None:
    """Publish exactly one verified quote passage for an approved rule.

    All three provenance fields must still match the staged row. This prevents
    a stale or tampered metadata mapping from publishing unrelated text.
    """
    quote = source_quote.strip() if isinstance(source_quote, str) else ""
    if not quote:
        raise RAGIndexingFailed("Approved generated rule has no source_quote")
    db: Session = SessionLocal()
    try:
        result = db.execute(
            text(
                """
                UPDATE rag_source_docs
                SET derived_rule_ids = (
                  SELECT ARRAY(
                    SELECT DISTINCT unnest(
                      derived_rule_ids || ARRAY[CAST(:rid AS UUID)]
                    )
                  )
                )
                WHERE id = CAST(:passage_id AS UUID)
                  AND document_id = CAST(:document_id AS UUID)
                  AND text = :source_quote
                """
            ),
            {
                "rid": str(rule_id),
                "passage_id": str(passage_id),
                "document_id": str(document_id),
                "source_quote": quote,
            },
        )
        if result.rowcount != 1:
            raise RAGIndexingFailed(
                "Exact source evidence mapping was missing; approval was refused"
            )
        db.commit()
    except RAGIndexingFailed:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error("link_rule_to_source_quote failed: %s", e)
        raise RAGIndexingFailed(str(e)) from e
    finally:
        db.close()
