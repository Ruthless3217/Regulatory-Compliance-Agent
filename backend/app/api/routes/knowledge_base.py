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
