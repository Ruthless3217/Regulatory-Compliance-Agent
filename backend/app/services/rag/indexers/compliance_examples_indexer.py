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
        if len(vectors) != len(rows):
            raise RAGIndexingFailed(
                f"embedder returned {len(vectors)} vectors for {len(rows)} rows"
            )
        docs = [_row_to_doc(r, v) for r, v in zip(rows, vectors)]
        await store.upsert("rag_compliance_examples", docs)
        logger.info(f"Indexed {len(docs)} precedents into rag_compliance_examples")
        return len(docs)
    except RAGDegraded as e:
        raise RAGIndexingFailed(str(e)) from e
