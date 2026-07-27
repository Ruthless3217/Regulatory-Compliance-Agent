"""
Submissions API Routes

Handles document upload and submission management.
"""
import os
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.submission import Submission
from app.config import settings
from app.auth.dependencies import require
from app.services.document_type import (
    normalize_document_type,
    classify_document_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/submissions", tags=["Submissions"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/html": "html",
    "text/markdown": "markdown",
    "text/plain": "text",
}


class ClassifyRequest(BaseModel):
    content: str


@router.post("/classify")
async def classify_submission(
    payload: ClassifyRequest,
    user: dict = Depends(require("submission:create")),
):
    """Suggest a semantic document type for the pasted/extracted content.

    The submission form calls this to pre-fill the document-type picker; the user
    confirms or overrides before running analysis (hybrid — Workstream A). The
    result is a suggestion only and never bypasses the user's confirmed choice.
    """
    suggested = await classify_document_type(payload.content or "")
    return {"document_type": suggested}


@router.post("")
async def create_submission(
    title: str = Form(...),
    content_type: str = Form(default="text"),
    content: Optional[str] = Form(default=None),
    document_type: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db)
):
    """
    Create a new submission for compliance analysis.
    Accepts either raw text content or a file upload.
    """
    file_path = None
    # Normalize the (user-confirmed) document type; an unrecognized value stores
    # NULL, which the analysis gate treats as strict (product) — fail-closed.
    document_type = normalize_document_type(document_type)

    # Handle file upload
    if file and file.filename:
        # Determine content type from file
        mime_type = file.content_type or ""
        detected_type = ALLOWED_CONTENT_TYPES.get(mime_type, content_type)

        # Save file
        os.makedirs(settings.upload_dir, exist_ok=True)
        file_id = str(uuid.uuid4())
        ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "txt"
        file_path = os.path.join(settings.upload_dir, f"{file_id}.{ext}")

        file_size = 0
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                if file_size > settings.max_upload_size:
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)

        content_type = detected_type

    submission = Submission(
        title=title,
        content_type=content_type,
        original_content=content,
        file_path=file_path,
        status="uploaded",
        submitted_by=getattr(user, "id", None),
        document_type=document_type,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    import asyncio
    from app.services.observability import audit
    asyncio.create_task(audit.record("submission_created", actor=user, target_type="submission", target_id=str(submission.id), metadata={"title": submission.title}))

    return {
        "id": str(submission.id),
        "title": submission.title,
        "content_type": submission.content_type,
        "document_type": submission.document_type,
        "status": submission.status,
        "submitted_at": submission.submitted_at.isoformat()
    }


@router.get("")
async def list_submissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """List all submissions."""
    submissions = db.query(Submission).offset(skip).limit(limit).all()
    total = db.query(Submission).count()

    return {
        "total": total,
        "submissions": [
            {
                "id": str(s.id),
                "title": s.title,
                "content_type": s.content_type,
                "status": s.status,
                "approval_status": s.approval_status,
                "submitted_at": s.submitted_at.isoformat()
            }
            for s in submissions
        ]
    }


@router.get("/{submission_id}")
async def get_submission(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """Get a specific submission by ID."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    return {
        "id": str(submission.id),
        "title": submission.title,
        "content_type": submission.content_type,
        "document_type": submission.document_type,
        "original_content": submission.original_content,
        "status": submission.status,
        "approval_status": submission.approval_status,
        "submitted_at": submission.submitted_at.isoformat()
    }


@router.delete("/{submission_id}")
async def delete_submission(
    submission_id: str,
    user: dict = Depends(require("submission:delete")),
    db: Session = Depends(get_db)
):
    """Delete a submission."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Clean up file if exists
    if submission.file_path and os.path.exists(submission.file_path):
        os.remove(submission.file_path)

    db.delete(submission)
    db.commit()

    import asyncio
    from app.services.observability import audit
    asyncio.create_task(audit.record("submission_deleted", actor=user, target_type="submission", target_id=submission_id))

    return {"message": "Submission deleted", "id": submission_id}
