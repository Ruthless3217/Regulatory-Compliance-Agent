"""
Document Comparison API Routes

Upload two versions of a document (or paste text) and get a persisted,
word-level diff between them. The text diff is pure text processing and runs
synchronously; a pixel-faithful page render (PDF-only) runs as a background
task and is polled via the render_status field.
"""
import os
import shutil
import logging
import re
import uuid
from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Body,
    BackgroundTasks, Response,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from app.database import get_db
from app.models.document_comparison import DocumentComparison
from app.models.comparison_annotation import ComparisonAnnotation
from app.config import settings
from app.services.comparison_service import extract_segments, build_diff, match_query_in_words
from app.services.render_orchestrator import run_render, renders_dir
from app.services import export_service
from app.auth.dependencies import require

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comparisons", tags=["Comparisons"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "text",
}

EXTENSION_CONTENT_TYPES = {
    "docx": "docx",
    "pdf": "pdf",
}

_PDF_EXPORT_KINDS = {"old-highlighted.pdf", "new-highlighted.pdf", "side-by-side.pdf"}
_EXPORT_MEDIA = {
    "changes-report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "old-highlighted.pdf": "application/pdf",
    "new-highlighted.pdf": "application/pdf",
    "side-by-side.pdf": "application/pdf",
    "bundle.zip": "application/zip",
}


class AnnotationIn(BaseModel):
    change_id: str
    note: Optional[str] = None
    tags: List[str] = []


def _detect_content_type(mime: str, filename: str) -> str:
    """Determine the logical content type ("pdf" | "docx" | "text") for an upload.

    The browser-supplied MIME type is trusted first, but browsers (notably on
    Windows without Office installed) often send a generic MIME such as
    "application/octet-stream" for .docx/.pdf files. In that case, fall back
    to the file extension so we don't silently read a binary file as text.
    """
    recognized = ALLOWED_CONTENT_TYPES.get(mime or "")
    if recognized:
        return recognized

    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return EXTENSION_CONTENT_TYPES.get(ext, "text")


def _sanitize_ext(filename: Optional[str]) -> str:
    """Extract a safe, alphanumeric-only file extension from a raw filename, defaulting to 'txt'."""
    raw_ext = filename.rsplit(".", 1)[-1] if filename and "." in filename else "txt"
    ext = re.sub(r"[^A-Za-z0-9]", "", raw_ext)
    return ext or "txt"


def _safe_title(title: Optional[str]) -> str:
    """Filesystem-safe slug for export download filenames."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "comparison").strip()).strip("-")
    return slug or "comparison"


async def _persist_upload(file: UploadFile):
    """Save an uploaded file under settings.upload_dir; returns (file_path, content_type)."""
    detected_type = _detect_content_type(file.content_type or "", file.filename or "")
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = _sanitize_ext(file.filename)
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


def _both_pdf(c: DocumentComparison) -> bool:
    return (
        c.old_content_type == "pdf"
        and c.new_content_type == "pdf"
        and bool(c.old_file_path)
        and bool(c.new_file_path)
    )


def _serialize_annotation(a: ComparisonAnnotation) -> dict:
    return {
        "change_id": a.change_id,
        "note": a.note,
        "tags": list(a.tags or []),
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _serialize(
    c: DocumentComparison,
    include_diff: bool = False,
    annotations: Optional[List[ComparisonAnnotation]] = None,
) -> dict:
    data = {
        "id": str(c.id),
        "title": c.title,
        "old_content_type": c.old_content_type,
        "new_content_type": c.new_content_type,
        "old_filename": c.old_filename,
        "new_filename": c.new_filename,
        "status": c.status,
        "error_message": c.error_message,
        "render_status": c.render_status,
        "render_error": c.render_error,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if include_diff:
        data["diff_result"] = c.diff_result
        data["render_result"] = c.render_result
    if annotations is not None:
        data["annotations"] = [_serialize_annotation(a) for a in annotations]
    return data


def _search_pdf(pdf_path: str, q: str, cap: int = 200) -> List[dict]:
    """Scan every page of a PDF for `q`, returning positioned hits (capped)."""
    import pdfplumber

    hits: List[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words() or []
            hits.extend(match_query_in_words(words, q, page_no, cap - len(hits)))
            if len(hits) >= cap:
                break
    return hits[:cap]


@router.post("")
async def create_comparison(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    old_content: Optional[str] = Form(default=None),
    new_content: Optional[str] = Form(default=None),
    old_file: Optional[UploadFile] = File(default=None),
    new_file: Optional[UploadFile] = File(default=None),
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Create a comparison: save any uploads, compute the diff inline, and (PDF-only) schedule a render."""
    has_old = bool(old_file and old_file.filename) or bool((old_content or "").strip())
    has_new = bool(new_file and new_file.filename) or bool((new_content or "").strip())
    if not has_old:
        raise HTTPException(status_code=400, detail="Provide old_file or old_content")
    if not has_new:
        raise HTTPException(status_code=400, detail="Provide new_file or new_content")

    old_file_path = None
    new_file_path = None
    old_filename = None
    new_filename = None
    old_content_type = "text"
    new_content_type = "text"

    if old_file and old_file.filename:
        old_file_path, old_content_type = await _persist_upload(old_file)
        old_filename = old_file.filename
    if new_file and new_file.filename:
        new_file_path, new_content_type = await _persist_upload(new_file)
        new_filename = new_file.filename

    comparison = DocumentComparison(
        title=title,
        old_content_type=old_content_type,
        new_content_type=new_content_type,
        old_file_path=old_file_path,
        new_file_path=new_file_path,
        old_filename=old_filename,
        new_filename=new_filename,
        old_original_content=old_content,
        new_original_content=new_content,
        status="processing",
        created_by=getattr(user, "id", None),
    )

    try:
        old_segments = extract_segments(old_file_path, old_content_type, old_content)
        new_segments = extract_segments(new_file_path, new_content_type, new_content)
        comparison.diff_result = build_diff(old_segments, new_segments)
        comparison.status = "completed"
    except Exception as e:
        logger.error(f"Comparison failed for '{title}': {e}")
        comparison.status = "failed"
        comparison.error_message = str(e)

    will_render = comparison.status == "completed" and _both_pdf(comparison)
    comparison.render_status = "processing" if will_render else "skipped"

    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    if will_render:
        background_tasks.add_task(run_render, str(comparison.id))

    return _serialize(comparison, include_diff=True, annotations=[])


@router.get("")
async def list_comparisons(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require("comparison:use")),
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
async def get_comparison(
    comparison_id: str,
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Get a comparison, including its full diff result, render overlay, and annotations."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    annotations = (
        db.query(ComparisonAnnotation)
        .filter(ComparisonAnnotation.comparison_id == comparison.id)
        .all()
    )
    return _serialize(comparison, include_diff=True, annotations=annotations)


@router.get("/{comparison_id}/pages/{side}/{n}")
async def get_comparison_page(
    comparison_id: str,
    side: str,
    n: int,
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Stream one rendered page PNG for the pixel document view."""
    if side not in ("old", "new"):
        raise HTTPException(status_code=422, detail="side must be 'old' or 'new'")
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    path = os.path.join(renders_dir(str(comparison.id)), side, f"page-{n:04d}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(path, media_type="image/png")


@router.get("/{comparison_id}/search")
async def search_comparison(
    comparison_id: str,
    side: str = Query(...),
    q: str = Query(..., min_length=2),
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Positioned text search over one side's stored PDF (PDF-only)."""
    if side not in ("old", "new"):
        raise HTTPException(status_code=422, detail="side must be 'old' or 'new'")
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    content_type = comparison.old_content_type if side == "old" else comparison.new_content_type
    path = comparison.old_file_path if side == "old" else comparison.new_file_path
    if content_type != "pdf" or not path or not os.path.exists(path):
        raise HTTPException(status_code=409, detail="Search is only available for PDF documents")
    return {"hits": _search_pdf(path, q)}


@router.post("/{comparison_id}/annotations")
async def upsert_annotation(
    comparison_id: str,
    body: AnnotationIn = Body(...),
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Create or update the note/tags for one change."""
    change_id = (body.change_id or "").strip()
    if not change_id:
        raise HTTPException(status_code=422, detail="change_id is required")
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")

    ann = (
        db.query(ComparisonAnnotation)
        .filter(
            ComparisonAnnotation.comparison_id == comparison.id,
            ComparisonAnnotation.change_id == change_id,
        )
        .first()
    )
    if ann is None:
        ann = ComparisonAnnotation(
            comparison_id=comparison.id,
            change_id=change_id,
            created_by=getattr(user, "id", None),
        )
        db.add(ann)
    ann.note = body.note
    ann.tags = list(body.tags or [])
    db.commit()
    db.refresh(ann)
    return _serialize_annotation(ann)


@router.delete("/{comparison_id}/annotations/{change_id}")
async def delete_annotation(
    comparison_id: str,
    change_id: str,
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Remove the annotation for one change (idempotent)."""
    ann = (
        db.query(ComparisonAnnotation)
        .filter(
            ComparisonAnnotation.comparison_id == comparison_id,
            ComparisonAnnotation.change_id == change_id,
        )
        .first()
    )
    if ann:
        db.delete(ann)
        db.commit()
    return {"message": "Annotation deleted", "change_id": change_id}


@router.get("/{comparison_id}/export/{kind}")
async def export_comparison(
    comparison_id: str,
    kind: str,
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Generate and stream one export artifact."""
    if kind not in _EXPORT_MEDIA:
        raise HTTPException(status_code=404, detail="Unknown export kind")
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    if kind in _PDF_EXPORT_KINDS and comparison.render_status != "completed":
        raise HTTPException(status_code=409, detail="Document rendering unavailable for this comparison")

    annotations = (
        db.query(ComparisonAnnotation)
        .filter(ComparisonAnnotation.comparison_id == comparison.id)
        .all()
    )
    try:
        if kind == "changes-report.docx":
            data = export_service.changes_report_docx(comparison, annotations)
        elif kind == "old-highlighted.pdf":
            data = export_service.highlighted_pdf(comparison, "old")
        elif kind == "new-highlighted.pdf":
            data = export_service.highlighted_pdf(comparison, "new")
        elif kind == "side-by-side.pdf":
            data = export_service.side_by_side_pdf(comparison)
        else:  # bundle.zip
            data = export_service.bundle_zip(comparison, annotations)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    filename = f"{_safe_title(comparison.title)}-{kind}"
    return Response(
        content=data,
        media_type=_EXPORT_MEDIA[kind],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{comparison_id}/rerun")
async def rerun_comparison(
    comparison_id: str,
    background_tasks: BackgroundTasks,
    swap: bool = Form(False),
    old_content: Optional[str] = Form(default=None),
    new_content: Optional[str] = Form(default=None),
    old_file: Optional[UploadFile] = File(default=None),
    new_file: Optional[UploadFile] = File(default=None),
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Adjust Comparison: replace a side and/or swap sides, then recompute in place.

    Clears all annotations (change ids are not stable across a re-run). 409 while
    a render is still processing.
    """
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")
    if comparison.render_status == "processing":
        raise HTTPException(status_code=409, detail="A render is still in progress")

    if old_file and old_file.filename:
        comparison.old_file_path, comparison.old_content_type = await _persist_upload(old_file)
        comparison.old_original_content = None
        comparison.old_filename = old_file.filename
    elif old_content is not None and old_content.strip():
        comparison.old_original_content = old_content
        comparison.old_content_type = "text"
        comparison.old_file_path = None
        comparison.old_filename = None

    if new_file and new_file.filename:
        comparison.new_file_path, comparison.new_content_type = await _persist_upload(new_file)
        comparison.new_original_content = None
        comparison.new_filename = new_file.filename
    elif new_content is not None and new_content.strip():
        comparison.new_original_content = new_content
        comparison.new_content_type = "text"
        comparison.new_file_path = None
        comparison.new_filename = None

    if swap:
        comparison.old_file_path, comparison.new_file_path = comparison.new_file_path, comparison.old_file_path
        comparison.old_content_type, comparison.new_content_type = comparison.new_content_type, comparison.old_content_type
        comparison.old_filename, comparison.new_filename = comparison.new_filename, comparison.old_filename
        comparison.old_original_content, comparison.new_original_content = (
            comparison.new_original_content,
            comparison.old_original_content,
        )

    try:
        old_segments = extract_segments(
            comparison.old_file_path, comparison.old_content_type, comparison.old_original_content
        )
        new_segments = extract_segments(
            comparison.new_file_path, comparison.new_content_type, comparison.new_original_content
        )
        comparison.diff_result = build_diff(old_segments, new_segments)
        comparison.status = "completed"
        comparison.error_message = None
    except Exception as e:
        logger.error(f"Re-run comparison failed for '{comparison.title}': {e}")
        comparison.status = "failed"
        comparison.error_message = str(e)
        comparison.diff_result = None

    # Change ids do not survive a re-run — drop every note/tag.
    db.query(ComparisonAnnotation).filter(
        ComparisonAnnotation.comparison_id == comparison.id
    ).delete()

    # Wipe stale rendered images and reset the render state.
    render_dir = renders_dir(str(comparison.id))
    if os.path.isdir(render_dir):
        shutil.rmtree(render_dir, ignore_errors=True)
    comparison.render_result = None
    comparison.render_error = None
    will_render = comparison.status == "completed" and _both_pdf(comparison)
    comparison.render_status = "processing" if will_render else "skipped"

    db.commit()
    db.refresh(comparison)

    if will_render:
        background_tasks.add_task(run_render, str(comparison.id))

    return _serialize(comparison, include_diff=True, annotations=[])


@router.delete("/{comparison_id}")
async def delete_comparison(
    comparison_id: str,
    user: dict = Depends(require("comparison:use")),
    db: Session = Depends(get_db),
):
    """Delete a comparison and any files it saved to disk (uploads + rendered pages)."""
    comparison = db.query(DocumentComparison).filter(DocumentComparison.id == comparison_id).first()
    if not comparison:
        raise HTTPException(status_code=404, detail="Comparison not found")

    for path in (comparison.old_file_path, comparison.new_file_path):
        if path and os.path.exists(path):
            os.remove(path)

    render_dir = renders_dir(str(comparison.id))
    if os.path.isdir(render_dir):
        shutil.rmtree(render_dir, ignore_errors=True)

    db.delete(comparison)
    db.commit()
    return {"message": "Comparison deleted", "id": comparison_id}
