"""Chunks indexer — mirrors ContentChunk rows into rag_chunks.

Called from preprocess_node after chunks land in Postgres. The status flips
from 'analyzing' to 'analyzed' at the end of the workflow (see
`mark_submission_analyzed`), which is the signal that the chunks become
eligible for cross-submission similarity search.
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.content_chunk import ContentChunk
from app.services.rag.errors import RAGDegraded, RAGIndexingFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import VectorDoc

logger = logging.getLogger(__name__)


def _chunk_to_doc(
 chunk: ContentChunk,
 embedding: List[float],
 submission_status: str,
 submission_summary: Optional[str],
) -> VectorDoc:
 meta = chunk.chunk_metadata or {}
 return VectorDoc(
 id=str(chunk.id),
 embedding=embedding,
 fields={
 "submission_id": str(chunk.submission_id),
 "chunk_index": chunk.chunk_index,
 "page_number": meta.get("page_number"),
 "text": chunk.text,
 "submission_status": submission_status,
 "submission_summary": submission_summary,
 },
 )


async def upsert_chunks_for_submission(
 submission_id: uuid.UUID | str,
 db: Session,
 submission_status: str = "analyzing",
 submission_summary: Optional[str] = None,
) -> int:
 chunks: List[ContentChunk] = (
 db.query(ContentChunk)
 .filter(ContentChunk.submission_id == str(submission_id))
 .order_by(ContentChunk.chunk_index)
 .all()
 )
 if not chunks:
 return 0

 embedder = get_embedder()
 store = get_vector_store()
 try:
 vectors = await embedder.embed([c.text for c in chunks])
 docs = [
 _chunk_to_doc(c, v, submission_status, submission_summary)
 for c, v in zip(chunks, vectors)
 ]
 await store.upsert("rag_chunks", docs)
 logger.info(f"Indexed {len(docs)} chunks for submission {submission_id}")
 return len(docs)
 except RAGDegraded as e:
 raise RAGIndexingFailed(str(e)) from e


async def mark_submission_analyzed(
 submission_id: uuid.UUID | str,
 db: Session,
 summary: Optional[str] = None,
) -> int:
 """Re-upsert the submission's chunks with status='analyzed'.

 Done as a re-upsert (rather than an UPDATE) so we don't bypass the
 VectorStore protocol — keeps Azure Search and pgvector treated identically.
 """
 return await upsert_chunks_for_submission(
 submission_id=submission_id,
 db=db,
 submission_status="analyzed",
 submission_summary=summary,
 )


async def delete_chunks_for_submission(submission_id: uuid.UUID | str, db: Session) -> int:
 chunks = (
 db.query(ContentChunk)
 .filter(ContentChunk.submission_id == str(submission_id))
 .all()
 )
 if not chunks:
 return 0
 store = get_vector_store()
 try:
 await store.delete("rag_chunks", [str(c.id) for c in chunks])
 return len(chunks)
 except RAGDegraded as e:
 raise RAGIndexingFailed(str(e)) from e
