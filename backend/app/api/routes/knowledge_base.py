"""Knowledge-base endpoints — ingest precedent corpus + report stats.

Projection endpoint (GET /knowledge-base/projection) is added in a later task.
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.knowledge_base_ingestion import get_kb_ingestion_service
from app.services.rag.errors import RAGDegraded, RAGEmbedFailed
from app.services.rag.factory import get_embedder, get_vector_store
from app.services.vector_projection import compute_projection
from app.auth.dependencies import require

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])


def resolve_ingest_path(folder_path: str, root: str) -> str:
    """Resolve a requested ingest folder and confine it to `root`.

    Returns the realpath of `folder_path` if it is `root` or a descendant of
    it; raises ValueError otherwise. This blocks path traversal and arbitrary
    server-side file reads via the ingest endpoint (audit C8).
    """
    root_real = os.path.realpath(root)
    target = os.path.realpath(folder_path)
    try:
        common = os.path.commonpath([target, root_real])
    except ValueError:
        # Different drives / unrelated roots → outside.
        raise ValueError(f"folder_path is outside the allowed ingest root: {folder_path}")
    if common != root_real:
        raise ValueError(f"folder_path is outside the allowed ingest root: {folder_path}")
    return target


class IngestRequest(BaseModel):
    folder_path: str = Field(..., description="Folder of _rl JSON files, within the configured ingest root")
    preview: bool = False
    limit: Optional[int] = Field(None, ge=1)


@router.post("/ingest")
async def ingest_knowledge_base(req: IngestRequest, user: dict = Depends(require("knowledgebase:view"))):
    try:
        safe_path = resolve_ingest_path(req.folder_path, settings.kb_ingest_root)
    except ValueError:
        # Do not echo the attempted path back; just refuse.
        raise HTTPException(status_code=400, detail="folder_path is outside the allowed ingest root")
    if not os.path.isdir(safe_path):
        raise HTTPException(status_code=400, detail="Folder not found within ingest root")
    req.folder_path = safe_path
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
        raise HTTPException(status_code=500, detail="Ingestion failed; see server logs.")


@router.get("/stats")
async def knowledge_base_stats(user: dict = Depends(require("knowledgebase:view"))):
    svc = get_kb_ingestion_service()
    try:
        return svc.get_stats()
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        raise HTTPException(status_code=500, detail=f"Stats failed: {e}")


@router.get("/search")
async def knowledge_base_search(
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=50),
    user: dict = Depends(require("knowledgebase:view"))
):
    """Hybrid-search the precedent corpus (rag_compliance_examples).

    Mirrors the debug/rag/search embed→hybrid_search path; embed or store
    failures surface as HTTP 503.
    """
    embedder = get_embedder()
    store = get_vector_store()
    try:
        qvec = (await embedder.embed([q], input_type="search_query"))[0]
    except RAGEmbedFailed as e:
        raise HTTPException(status_code=503, detail=f"embedder failed: {e}")

    try:
        hits = await store.hybrid_search(
            index="rag_compliance_examples",
            query_text=q,
            query_vector=qvec,
            top_k=k,
            recall_pool=settings.rag_recall_pool,
            rrf_k=settings.rag_rrf_k,
            filters=None,
        )
    except RAGDegraded as e:
        raise HTTPException(status_code=503, detail=f"vector store failed: {e}")

    def _fields(h):
        f = h.fields or {}
        return {
            "reviewer_name": f.get("reviewer_name"),
            "comment_text": f.get("comment_text"),
            "chunk_text": f.get("chunk_text"),
            "anchor_text": f.get("anchor_text"),
            "final_text_chunk": f.get("final_text_chunk"),
            "violation_category": f.get("violation_category"),
            "severity": f.get("severity"),
            "document_id": f.get("document_id"),
            "source_file": f.get("source_file"),
        }

    return {
        "query": q,
        "results": [
            {"id": h.id, "score": h.score, "fields": _fields(h)} for h in hits
        ],
    }


@router.get("/projection")
async def knowledge_base_projection(
    method: str = Query("umap", pattern="^(umap|pca)$"),
    refresh: bool = False,
    user: dict = Depends(require("knowledgebase:view"))
):
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, partial(compute_projection, method=method, refresh=refresh)
        )
    except Exception as e:
        logger.error(f"Projection failed: {e}")
        raise HTTPException(status_code=500, detail=f"Projection failed: {e}")
