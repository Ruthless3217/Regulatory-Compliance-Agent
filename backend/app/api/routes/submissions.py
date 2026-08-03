"""
Submissions API Routes

Handles document upload and submission management.
"""
import os
import re
import shutil
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.document_comment import DocumentComment
from app.models.violation import Violation
from app.config import settings
from app.auth.dependencies import require
from app.schemas.submission import (
    SubmissionRevisionCreate,
    DocumentCommentCreate,
    DocumentCommentUpdate,
)
from app.services.submission_render_service import RENDERABLE_CONTENT_TYPES, renders_dir, run_render
from app.services import submission_export_service
from app.services import export_common
from app.services import lexical_document_service
from app.services.gotenberg_client import GotenbergError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/submissions", tags=["Submissions"])

ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/html": "html",
    "text/markdown": "markdown",
    "text/plain": "text",
}
ALLOWED_PRODUCT_LINES = {
    "global", "term", "ulip", "rider", "group", "savings_endowment",
    "pension_annuity", "par", "non_par",
}


def _validated_product_line(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_PRODUCT_LINES:
        raise HTTPException(
            status_code=400,
            detail="product_line must be an explicit supported scope or global",
        )
    return normalized


@router.post("")
async def create_submission(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    content_type: str = Form(default="text"),
    product_line: str = Form(...),
    content: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db)
):
    """
    Create a new submission for compliance analysis.
    Accepts either raw text content or a file upload.
    """
    file_path = None
    product_line = _validated_product_line(product_line)

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

    # Render every format that has a page layout — PDF directly, DOCX through
    # Gotenberg. Gating on "pdf" here left DOCX uploads stamped "skipped" and
    # showing flat extracted text until the self-heal in get_submission caught
    # them on first open. RENDERABLE_CONTENT_TYPES is the one list.
    will_render = content_type in RENDERABLE_CONTENT_TYPES and file_path is not None

    submission = Submission(
        title=title,
        content_type=content_type,
        original_content=content,
        file_path=file_path,
        status="uploaded",
        product_line=product_line,
        page_render_status="processing" if will_render else "skipped",
        submitted_by=getattr(user, "id", None),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    if will_render:
        background_tasks.add_task(run_render, str(submission.id))

    import asyncio
    from app.services.observability import audit
    asyncio.create_task(audit.record("submission_created", actor=user, target_type="submission", target_id=str(submission.id), metadata={"title": submission.title}))

    return {
        "id": str(submission.id),
        "title": submission.title,
        "content_type": submission.content_type,
        "status": submission.status,
        "product_line": submission.product_line,
        "page_render_status": submission.page_render_status,
        "submitted_at": submission.submitted_at.isoformat()
    }


@router.get("")
async def list_submissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """List submissions, newest first.

    The ORDER BY is load-bearing, not cosmetic. Without it Postgres returns
    heap order, so `skip`/`limit` slice an arbitrary window: with more
    submissions than `limit`, a newly uploaded document could be absent from
    page 1 entirely, and paging could show the same row twice while never
    showing another. Every consumer of this route reads it as a recency-ordered
    list — the inbox, the retrieval inspector's picker — so the ordering
    belongs here rather than in each caller.
    """
    submissions = (
        db.query(Submission)
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(Submission).count()

    return {
        "total": total,
        "submissions": [
            {
                "id": str(s.id),
                "title": s.title,
                "content_type": s.content_type,
                "status": s.status,
                "product_line": s.product_line,
                "approval_status": s.approval_status,
                "submitted_at": s.submitted_at.isoformat()
            }
            for s in submissions
        ]
    }


@router.get("/{submission_id}")
async def get_submission(
    submission_id: str,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """Get a specific submission by ID."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Everything uploaded before DOCX rendering existed is stamped "skipped" and
    # would otherwise stay on the extracted-text pane forever. Re-render those
    # once, on first open, rather than shipping a one-off backfill script.
    if (
        submission.page_render_status == "skipped"
        and submission.content_type in RENDERABLE_CONTENT_TYPES
        and submission.file_path
    ):
        submission.page_render_status = "pending"
        db.commit()
        background_tasks.add_task(run_render, str(submission.id))

    return {
        "id": str(submission.id),
        "title": submission.title,
        "content_type": submission.content_type,
        # original_content is the immutable copy the violations were graded
        # against; current_content is the reviewer's working copy (0025) and is
        # NULL until someone edits. Returning both lets the review pane seed its
        # editor from `current_content ?? original_content` in one call, instead
        # of fetching the revision list on every mount just to find the latest.
        "original_content": submission.original_content,
        "current_content": submission.current_content,
        # Working document. `lexical_state` is authoritative once present;
        # `import_html` seeds the editor the first time, and is None for any
        # submission with no importable upload (which keeps the text pane).
        "lexical_state": submission.lexical_state,
        "import_html": (
            None if submission.lexical_state
            else lexical_document_service.build_import_html(submission)
        ),
        "status": submission.status,
        "product_line": submission.product_line,
        "approval_status": submission.approval_status,
        "page_render_status": submission.page_render_status,
        "submitted_at": submission.submitted_at.isoformat()
    }


@router.get("/{submission_id}/pages/{n}")
async def get_submission_page(
    submission_id: str,
    n: int,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db)
):
    """Stream one rendered page PNG for the pixel document view.

    Single-sided mirror of `GET /comparisons/{id}/pages/{side}/{n}` — reuses
    the same renderer, just one document instead of an old/new pair.
    """
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    path = os.path.join(renders_dir(str(submission.id)), f"page-{n:04d}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Content revisions — the one mutation primitive behind manual edits,
# apply-fix, bulk-apply-fixes, and restore (migration 0026).
# ---------------------------------------------------------------------------

def _serialize_revision(r: SubmissionRevision) -> dict:
    return {
        "id": str(r.id),
        "submission_id": str(r.submission_id),
        "revision_number": r.revision_number,
        "content": r.content,
        "source": r.source,
        "note": r.note,
        "applied_violation_ids": [str(v) for v in (r.applied_violation_ids or [])],
        "created_by": str(r.created_by) if r.created_by else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _next_revision_number(db: Session, submission_id) -> int:
    """1-based, per submission. Mirrors run_tracker.open_run's count+1 idiom —
    no extra app-level locking; UNIQUE(submission_id, revision_number) is the
    backstop against a genuine race."""
    existing = (
        db.query(SubmissionRevision)
        .filter(SubmissionRevision.submission_id == submission_id)
        .all()
    )
    return max((r.revision_number for r in existing), default=0) + 1


@router.post("/{submission_id}/revisions")
async def create_revision(
    submission_id: str,
    body: SubmissionRevisionCreate = Body(...),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db),
):
    """Record a new content revision and make it the submission's current
    content. `source` distinguishes manual_edit/apply_fix/bulk_apply_fixes/
    restore — restore is just this same endpoint re-posting an old revision's
    content with source='restore'."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    revision = SubmissionRevision(
        submission_id=submission.id,
        revision_number=_next_revision_number(db, submission.id),
        content=body.content,
        source=body.source,
        note=body.note,
        applied_violation_ids=body.applied_violation_ids or None,
        created_by=getattr(user, "id", None),
        lexical_state=body.lexical_state,
        lexical_html=body.lexical_html,
    )
    db.add(revision)
    submission.current_content = body.content

    # The working document moves with the revision. Only overwrite when the
    # client actually sent one, so a text-only save cannot blank it. State and
    # HTML are two views of one document — write both or neither, or export
    # would render a version the editor never shows.
    if body.lexical_state is not None:
        submission.lexical_state = body.lexical_state
        submission.lexical_html = body.lexical_html

    # Flip fix_applied on every violation this revision resolved, so the
    # reviewer UI's "Applied" badge/disabled-button state survives reload
    # instead of resetting on next fetch.
    if body.applied_violation_ids:
        applied_at = datetime.now(timezone.utc)
        for vid in body.applied_violation_ids:
            fixed = db.query(Violation).filter(Violation.id == vid).first()
            if fixed is not None:
                fixed.fix_applied = True
                fixed.fix_applied_at = applied_at
                db.add(fixed)

    db.commit()
    db.refresh(revision)
    return _serialize_revision(revision)


@router.get("/{submission_id}/revisions")
async def list_revisions(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """List every revision for a submission, oldest first."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    revisions = (
        db.query(SubmissionRevision)
        .filter(SubmissionRevision.submission_id == submission.id)
        .order_by(SubmissionRevision.revision_number)
        .all()
    )
    return {"revisions": [_serialize_revision(r) for r in revisions]}


@router.get("/{submission_id}/revisions/{revision_number}")
async def get_revision(
    submission_id: str,
    revision_number: int,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Fetch one revision by its 1-based number — lets the UI address a past
    version directly (e.g. to preview it before restoring)."""
    revision = (
        db.query(SubmissionRevision)
        .filter(
            SubmissionRevision.submission_id == submission_id,
            SubmissionRevision.revision_number == revision_number,
        )
        .first()
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")
    return _serialize_revision(revision)


# ---------------------------------------------------------------------------
# Document comments — freestanding reviewer notes anchored to a text
# selection (migration 0027). Mirrors comparisons.py's annotation shape, but
# each comment is its own row rather than a per-change-id upsert, since a
# submission's document has no comparison-style stable change id.
# ---------------------------------------------------------------------------

def _serialize_comment(c: DocumentComment) -> dict:
    return {
        "id": str(c.id),
        "submission_id": str(c.submission_id),
        "anchor_text": c.anchor_text,
        "page_number": c.page_number,
        "body": c.body,
        "resolved": c.resolved,
        "created_by": str(c.created_by) if c.created_by else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.post("/{submission_id}/comments")
async def create_comment(
    submission_id: str,
    body: DocumentCommentCreate = Body(...),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db),
):
    """Create a freestanding comment anchored to a text selection."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if not (body.body or "").strip():
        raise HTTPException(status_code=422, detail="body is required")

    comment = DocumentComment(
        submission_id=submission.id,
        anchor_text=body.anchor_text,
        page_number=body.page_number,
        body=body.body,
        resolved=False,
        created_by=getattr(user, "id", None),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment)


@router.get("/{submission_id}/comments")
async def list_comments(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """List every comment on a submission, oldest first."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    comments = (
        db.query(DocumentComment)
        .filter(DocumentComment.submission_id == submission.id)
        .order_by(DocumentComment.created_at)
        .all()
    )
    return {"comments": [_serialize_comment(c) for c in comments]}


@router.patch("/{submission_id}/comments/{comment_id}")
async def update_comment(
    submission_id: str,
    comment_id: str,
    body: DocumentCommentUpdate = Body(...),
    user: dict = Depends(require("submission:create")),
    db: Session = Depends(get_db),
):
    """Update a comment's body and/or resolved flag."""
    comment = (
        db.query(DocumentComment)
        .filter(
            DocumentComment.submission_id == submission_id,
            DocumentComment.id == comment_id,
        )
        .first()
    )
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if body.body is not None:
        comment.body = body.body
    if body.resolved is not None:
        comment.resolved = body.resolved
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment)


@router.delete("/{submission_id}/comments/{comment_id}")
async def delete_comment(
    submission_id: str,
    comment_id: str,
    user: dict = Depends(require("submission:delete")),
    db: Session = Depends(get_db),
):
    """Remove a comment (idempotent)."""
    comment = (
        db.query(DocumentComment)
        .filter(
            DocumentComment.submission_id == submission_id,
            DocumentComment.id == comment_id,
        )
        .first()
    )
    if comment:
        db.delete(comment)
        db.commit()
    return {"message": "Comment deleted", "id": comment_id}


# ---------------------------------------------------------------------------
# Export — clean/annotated/report/feedback-report copies (docx + pdf) plus a
# bundle.zip. Mirrors GET /comparisons/{id}/export/{kind}'s dispatch shape:
# an allow-list check, then one call into the export service, streamed back
# as an attachment.
# ---------------------------------------------------------------------------

_EXPORT_MEDIA = {
    "clean.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "clean.pdf": "application/pdf",
    "annotated.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "annotated.pdf": "application/pdf",
    "report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "report.pdf": "application/pdf",
    "feedback-report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "feedback-report.pdf": "application/pdf",
    "bundle.zip": "application/zip",
}


def _safe_title(title: Optional[str]) -> str:
    """Filesystem-safe slug for export download filenames (mirrors
    comparisons.py's helper of the same name)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "submission").strip()).strip("-")
    return slug or "submission"


@router.get("/{submission_id}/export/{kind}")
async def export_submission(
    submission_id: str,
    kind: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Generate and stream one export artifact."""
    if kind not in _EXPORT_MEDIA:
        raise HTTPException(status_code=404, detail="Unknown export kind")
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # An export must never pair a corrected document with the findings of the
    # version before the correction. Enforced here, not only in the UI banner,
    # because the export URL is directly reachable.
    if export_common.findings_are_stale(db, submission.id):
        raise HTTPException(
            status_code=409,
            detail="Document edited since the last analysis — re-run the compliance check before exporting",
        )

    try:
        data = submission_export_service.build_export(db, submission, kind)
    except GotenbergError as e:
        raise HTTPException(status_code=502, detail=f"PDF conversion unavailable: {e}")

    filename = f"{_safe_title(submission.title)}-{kind}"
    return Response(
        content=data,
        media_type=_EXPORT_MEDIA[kind],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    render_dir = renders_dir(str(submission.id))
    if os.path.isdir(render_dir):
        shutil.rmtree(render_dir, ignore_errors=True)

    db.delete(submission)
    db.commit()

    import asyncio
    from app.services.observability import audit
    asyncio.create_task(audit.record("submission_deleted", actor=user, target_type="submission", target_id=submission_id))

    return {"message": "Submission deleted", "id": submission_id}
