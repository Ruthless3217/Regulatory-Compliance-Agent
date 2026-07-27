"""Health + debug endpoints for the RAG layer.

GET  /health/rag        → embedder + vector store reachability + last index times
POST /debug/rag/search  → ad-hoc hybrid_search; safe for ops/troubleshooting
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.auth.dependencies import require

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RAG"], dependencies=[Depends(require("knowledgebase:view"))])


@router.get("/health/rag")
async def health_rag(db: Session = Depends(get_db)):
    embedder = get_embedder()
    store = get_vector_store()

    # Embedder probe — try a 1-token embed.
    embed_ok = False
    embed_err: Optional[str] = None
    try:
        await embedder.embed(["health"])
        embed_ok = True
    except Exception as e:
        embed_err = str(e)

    # Store reachability.
    try:
        store_ok = await store.health()
    except Exception as e:
        store_ok = False
        logger.debug(f"store.health raised: {e}")

    # Last-indexed timestamps per table (pgvector path only).
    last_indexed: Dict[str, Optional[str]] = {}
    if settings.rag_vector_backend == "pgvector":
        for tbl in ("rag_rules", "rag_chunks", "rag_source_docs"):
            try:
                col = "updated_at" if tbl != "rag_source_docs" else "uploaded_at"
                row = db.execute(text(f"SELECT MAX({col}) FROM {tbl}")).first()
                last_indexed[tbl] = row[0].isoformat() if row and row[0] else None
            except Exception:
                last_indexed[tbl] = None

    return {
        "backend": store.name,
        "embedder": embedder.name,
        "model": embedder.model,
        "dim": embedder.dim,
        "embedder_ok": embed_ok,
        "embedder_error": embed_err,
        "vector_store_ok": store_ok,
        "last_indexed_at": last_indexed,
    }


# --------------------------------------------------------- debug search ---

class DebugSearchRequest(BaseModel):
    index: Literal["rag_rules", "rag_chunks", "rag_source_docs"]
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    filters: Optional[Dict[str, Any]] = None


@router.post("/debug/rag/search")
async def debug_rag_search(req: DebugSearchRequest):
    embedder = get_embedder()
    store = get_vector_store()
    try:
        qvec = (await embedder.embed([req.query]))[0]
    except RAGEmbedFailed as e:
        raise HTTPException(status_code=503, detail=f"embedder failed: {e}")

    try:
        hits = await store.hybrid_search(
            index=req.index,
            query_text=req.query,
            query_vector=qvec,
            top_k=req.top_k,
            recall_pool=settings.rag_recall_pool,
            rrf_k=settings.rag_rrf_k,
            filters=req.filters,
        )
    except RAGDegraded as e:
        raise HTTPException(status_code=503, detail=f"vector store failed: {e}")

    return {
        "query": req.query,
        "index": req.index,
        "results": [
            {"id": h.id, "score": h.score, "fields": h.fields} for h in hits
        ],
    }
