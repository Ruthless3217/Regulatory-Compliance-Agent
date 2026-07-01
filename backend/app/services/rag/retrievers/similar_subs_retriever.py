"""Cross-submission similarity retriever.

Given a submission_id, finds the top-K prior analyzed submissions whose
chunks are most similar. Groups hits by submission_id; each submission's
score is the max chunk score (could be averaged — max gives "any single
strong overlap" which is what reviewers want for precedent).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.content_chunk import ContentChunk
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store

logger = logging.getLogger(__name__)


class SimilarSubmissionsRetriever:
 async def retrieve(
 self,
 submission_id: uuid.UUID | str,
 db: Session,
 top_k_submissions: Optional[int] = None,
 chunks_per_submission: int = 2,
 ) -> List[Dict[str, Any]]:
 K = top_k_submissions or settings.rag_top_k_similar

 # Build a representative query: concatenate this submission's chunks
 # (capped) and embed once.
 chunks: List[ContentChunk] = (
 db.query(ContentChunk)
 .filter(ContentChunk.submission_id == str(submission_id))
 .order_by(ContentChunk.chunk_index)
 .limit(6)
 .all()
 )
 if not chunks:
 return []

 joined = "\n\n".join(c.text for c in chunks)[:8000]

 embedder = get_embedder()
 store = get_vector_store()
 try:
 qvec = (await embedder.embed([joined], input_type="search_query"))[0]
 # Pull a wider net than K * chunks_per_submission so we can
 # de-duplicate by submission_id without losing diversity.
 hits = await store.vector_search(
 index="rag_chunks",
 query_vector=qvec,
 top_k=K * chunks_per_submission * 4,
 filters={"submission_status": "analyzed"},
 )
 except (RAGEmbedFailed, RAGDegraded) as e:
 logger.warning(f"similar submissions degraded: {e}")
 return []

 # Group by submission_id, exclude self.
 by_sub: Dict[str, List[Any]] = {}
 for h in hits:
 sid = str(h.fields.get("submission_id"))
 if not sid or sid == str(submission_id):
 continue
 by_sub.setdefault(sid, []).append(h)

 # Rank submissions by their best (max) chunk score, return top-K.
 ranked = sorted(
 by_sub.items(),
 key=lambda kv: max(h.score for h in kv[1]),
 reverse=True,
 )[:K]

 out: List[Dict[str, Any]] = []
 for sid, sub_hits in ranked:
 sub_hits.sort(key=lambda h: h.score, reverse=True)
 top_chunks = sub_hits[:chunks_per_submission]
 out.append({
 "submission_id": sid,
 "top_score": top_chunks[0].score,
 "submission_summary": top_chunks[0].fields.get("submission_summary"),
 "matching_chunks": [
 {
 "chunk_id": h.id,
 "chunk_index": h.fields.get("chunk_index"),
 "text": h.fields.get("text"),
 "score": h.score,
 }
 for h in top_chunks
 ],
 })
 return out


_singleton: Optional[SimilarSubmissionsRetriever] = None


def get_similar_submissions_retriever() -> SimilarSubmissionsRetriever:
 global _singleton
 if _singleton is None:
 _singleton = SimilarSubmissionsRetriever()
 return _singleton
