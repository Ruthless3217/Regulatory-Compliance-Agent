"""Cross-submission similarity search.

GET /submissions/{id}/similar
  → top-N prior analyzed submissions whose chunks resemble this one,
    with the matching chunks for each.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.submission import Submission
from app.services.rag.retrievers.similar_subs_retriever import (
    get_similar_submissions_retriever,
)

logger = logging.getLogger(__name__)

# Mounted at root; the path-prefix carries the resource name.
router = APIRouter(prefix="/submissions", tags=["Submissions"])


@router.get("/{submission_id}/similar")
async def get_similar_submissions(
    submission_id: UUID,
    top_k: int = Query(default=3, ge=1, le=20),
    chunks_per: int = Query(default=2, ge=1, le=10),
    db: Session = Depends(get_db),
):
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    retriever = get_similar_submissions_retriever()
    results = await retriever.retrieve(
        submission_id=submission_id,
        db=db,
        top_k_submissions=top_k,
        chunks_per_submission=chunks_per,
    )
    return {
        "submission_id": str(submission_id),
        "similar": results,
        "degraded": not results,
    }
