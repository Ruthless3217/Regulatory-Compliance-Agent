"""
Document Comparison API Routes

Upload two versions of a document (or paste text) and get a persisted,
word-level diff between them. No LLM calls — pure text processing, so
requests are handled synchronously.
"""
import os
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.document_comparison import DocumentComparison
from app.config import settings
from app.services.comparison_service import extract_paragraphs, build_diff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comparisons", tags=["Comparisons"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "text",
}


async def _persist_upload(file: UploadFile):
    """Save an uploaded file under settings.upload_dir; returns (file_path, content_type)."""
    mime_type = file.content_type or ""
    detected_type = ALLOWED_CONTENT_TYPES.get(mime_type, "text")
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = file.filename.rsplit(".", 1)[-1] if file.filename and "." in file.filename else "txt"
    file_path = os.path.join(settings.upload_dir, f"{file_id}.{ext}")

    file_size = 0
    with open(file_path, "wb") as f:
        while chunk := await file.read(8192):
            file_size += len(chunk)
            if file_size > settings.max_upload_size:
                f.close()
                os.remove(file_path)
                raise HTTPException(status_code=413, detail="File too large")
            f.write(chunk)
    return file_path, detected_type


def _serialize(c: DocumentComparison, include_diff: bool = False) -> dict:
    data = {
        "id": str(c.id),
        "title": c.title,
        "old_content_type": c.old_content_type,
        "new_content_type": c.new_content_type,
        "status": c.status,
        "error_message": c.error_message,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if include_diff:
        data["diff_result"] = c.diff_result
    return data


@router.post("")
async def create_comparison(
    title: str = Form(...),
    old_content: Optional[str] = Form(default=None),
    new_content: Optional[str] = Form(default=None),
    old_file: Optional[UploadFile] = File(default=None),
    new_file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """Create a comparison: saves any uploaded files, computes the diff inline, and persists it."""
    has_old = bool(old_file and old_file.filename) or bool((old_content or "").strip())
    has_new = bool(new_file and new_file.filename) or bool((new_content or "").strip())
    if not has_old:
        raise HTTPException(status_code=400, detail="Provide old_file or old_content")
    if not has_new:
        raise HTTPException(status_code=400, detail="Provide new_file or new_content")

    old_file_path = None
    new_file_path = None
    old_content_type = "text"
    new_content_type = "text"

    if old_file and old_file.filename:
        old_file_path, old_content_type = await _persist_upload(old_file)
    if new_file and new_file.filename:
        new_file_path, new_content_type = await _persist_upload(new_file)

    comparison = DocumentComparison(
        title=title,
        old_content_type=old_content_type,
        new_content_type=new_content_type,
        old_file_path=old_file_path,
        new_file_path=new_file_path,
        old_original_content=old_content,
        new_original_content=new_content,
        status="processing",
    )

    try:
        old_paragraphs = extract_paragraphs(old_file_path, old_content_type, old_content)
        new_paragraphs = extract_paragraphs(new_file_path, new_content_type, new_content)
        comparison.diff_result = build_diff(old_paragraphs, new_paragraphs)
        comparison.status = "completed"
    except Exception as e:
        logger.error(f"Comparison failed for '{title}': {e}")
        comparison.status = "failed"
        comparison.error_message = str(e)

    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    return _serialize(comparison, include_diff=True)


@router.get("")
async def list_comparisons(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List past comparisons, most recent first."""
    query = db.query(DocumentComparison).order_by(DocumentComparison.created_at.desc())
    total = query.count()
    comparisons = query.offset(skip).limit(limit).all()
    return {
        "total": total,
        "comparisons": [_serialize(c) for c in comparisons],
    }


@router.get("/{comparison_id}")
async def get_comparison(comparison_id: str, db: Session = Depends(get_db)):
    """Get a comparison, including its full diff result."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return _serialize(comparison, include_diff=True)


@router.delete("/{comparison_id}")
async def delete_comparison(comparison_id: str, db: Session = Depends(get_db)):
    """Delete a comparison and any files it saved to disk."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")

    for path in (comparison.old_file_path, comparison.new_file_path):
        if path and os.path.exists(path):
            os.remove(path)

    db.delete(comparison)
    db.commit()
    return {"message": "Comparison deleted", "id": comparison_id}
