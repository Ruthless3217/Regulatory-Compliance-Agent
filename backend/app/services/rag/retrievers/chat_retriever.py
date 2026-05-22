"""Chat retriever — assembles the bundle of relevant rules, chunks, source
passages, and linked violations for a /chat turn.

All three index queries run in parallel. Violations are joined in-process
by rule_id so chat only surfaces violations whose rule is actually in
the retrieved set.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.violation import Violation
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.rag.ports import SearchHit

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    relevant_rules: List[Dict[str, Any]] = field(default_factory=list)
    relevant_chunks: List[Dict[str, Any]] = field(default_factory=list)
    source_passages: List[Dict[str, Any]] = field(default_factory=list)
    linked_violations: List[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


def _hit_to_dict(h: SearchHit) -> Dict[str, Any]:
    return {"id": h.id, "score": h.score, **h.fields}


class ChatRetriever:
    async def retrieve(
        self,
        query: str,
        submission_id: uuid.UUID | str,
        db: Session,
        top_k_rules: Optional[int] = None,
        top_k_chunks: int = 3,
        top_k_source_passages: int = 2,
    ) -> ChatContext:
        K_rules = top_k_rules or settings.rag_top_k_chat
        embedder = get_embedder()
        store = get_vector_store()

        try:
            qvec = (await embedder.embed([query]))[0]
        except RAGEmbedFailed as e:
            logger.warning(f"Embed failed in chat retriever, returning empty context: {e}")
            return ChatContext(degraded=True)

        async def get_rules():
            try:
                return await store.hybrid_search(
                    index="rag_rules",
                    query_text=query,
                    query_vector=qvec,
                    top_k=K_rules,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                    filters={"is_active": True},
                )
            except RAGDegraded:
                return []

        async def get_chunks():
            try:
                return await store.hybrid_search(
                    index="rag_chunks",
                    query_text=query,
                    query_vector=qvec,
                    top_k=top_k_chunks,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                    filters={"submission_id": str(submission_id)},
                )
            except RAGDegraded:
                return []

        async def get_passages():
            try:
                return await store.hybrid_search(
                    index="rag_source_docs",
                    query_text=query,
                    query_vector=qvec,
                    top_k=top_k_source_passages,
                    recall_pool=settings.rag_recall_pool,
                    rrf_k=settings.rag_rrf_k,
                )
            except RAGDegraded:
                return []

        rules_hits, chunks_hits, passages_hits = await asyncio.gather(
            get_rules(), get_chunks(), get_passages()
        )

        degraded = not (rules_hits or chunks_hits or passages_hits)

        # Link violations whose rule_id is in the retrieved rules.
        rule_ids = {h.id for h in rules_hits}
        linked_violations: List[Dict[str, Any]] = []
        if rule_ids:
            rows = (
                db.query(Violation)
                .filter(Violation.rule_id.in_(rule_ids))
                .all()
            )
            for v in rows:
                linked_violations.append({
                    "id": str(v.id),
                    "rule_id": str(v.rule_id) if v.rule_id else None,
                    "category": v.category,
                    "severity": v.severity,
                    "description": v.description,
                    "current_text": v.current_text,
                    "suggested_fix": v.suggested_fix,
                })

        return ChatContext(
            relevant_rules=[_hit_to_dict(h) for h in rules_hits],
            relevant_chunks=[_hit_to_dict(h) for h in chunks_hits],
            source_passages=[_hit_to_dict(h) for h in passages_hits],
            linked_violations=linked_violations,
            degraded=degraded,
        )


_singleton: Optional[ChatRetriever] = None


def get_chat_retriever() -> ChatRetriever:
    global _singleton
    if _singleton is None:
        _singleton = ChatRetriever()
    return _singleton
