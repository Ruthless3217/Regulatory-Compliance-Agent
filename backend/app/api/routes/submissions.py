"""
Submissions API Routes

Handles document upload and submission management.
"""
import asyncio
import os
import re
import shutil
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.analysis_run import AnalysisRun
from app.models.compliance_check import ComplianceCheck
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.document_comment import DocumentComment
from app.models.violation import Violation
from app.config import settings
from app.auth.dependencies import require
from app.auth.visibility import get_visible_submission, visible_submission_filter
from app.services import assignment_service
from app.services.observability import audit
from app.schemas.submission import (
    SubmissionRevisionCreate,
    DocumentCommentCreate,
    DocumentCommentUpdate,
)
from app.services.submission_render_service import (
    RENDERABLE_CONTENT_TYPES,
    renders_dir,
    run_anchor,
    run_render,
)
from app.services import comparison_service
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
    asyncio.create_task(audit.record("submission_created", actor=user, target_type="submission", target_id=str(submission.id), scope_submission_id=submission.id, metadata={"title": submission.title}))

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
    # Bucket scoping. `None` means the caller (admin / super_admin) sees
    # everything; anyone else sees only what is assigned to them or what they
    # uploaded. `total` must carry the same clause or the pager counts rows the
    # caller will never be shown.
    scope = visible_submission_filter(user)

    listing = db.query(Submission)
    counter = db.query(Submission)
    if scope is not None:
        listing = listing.filter(scope)
        counter = counter.filter(scope)

    submissions = (
        listing
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = counter.count()

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
    submission = get_visible_submission(db, submission_id, user)

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
    elif submission.page_render_status == "completed":
        # Anything analysed before the analyzer started anchoring its own
        # findings has NULL anchor_page, and the page view draws no boxes for
        # it. Nothing else would ever revisit those, so re-anchor on first open
        # when not a single finding on the document has a box.
        anchored = (
            db.query(Violation.id)
            .join(ComplianceCheck, Violation.compliance_check_id == ComplianceCheck.id)
            .filter(
                ComplianceCheck.submission_id == submission.id,
                Violation.anchor_page.isnot(None),
            )
            .first()
        )
        if anchored is None:
            background_tasks.add_task(run_anchor, str(submission.id))

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
        # NOT the import HTML. Converting a long DOCX/PDF costs seconds of CPU,
        # and computing it here made opening a submission slow enough to time
        # out — while blocking the event loop for every other request, because
        # build_import_html is synchronous and this handler is async. The editor
        # fetches it from /import-html only when it actually needs to seed.
        "has_import_source": (
            submission.lexical_state is None
            and lexical_document_service.can_import(submission)
        ),
        "status": submission.status,
        "product_line": submission.product_line,
        "approval_status": submission.approval_status,
        "page_render_status": submission.page_render_status,
        "submitted_at": submission.submitted_at.isoformat()
    }


@router.get("/{submission_id}/import-html")
async def get_submission_import_html(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """HTML to seed the editor the first time this submission is opened.

    Its own endpoint, and off the event loop, because converting a long
    document takes seconds: inline in GET /submissions/{id} it made simply
    opening a document time out, and blocked every other request on the worker
    while it ran. Only the editor calls this, and only when there is no saved
    working document to load instead.

    Always answers with {html, status, reason}. `status` is what the editor
    reacts to — `imported`, `unavailable` (nothing to import) or `failed` (the
    conversion broke) — because `has_import_source` on GET /submissions/{id} is
    only a cheap "this looks importable" and cannot promise the conversion
    succeeds. A scanned PDF or a corrupt DOCX passes that check and arrives
    here as a failure, and a bare `html: null` left the reviewer facing a blank
    editable page with no way to tell what had happened.
    """
    submission = get_visible_submission(db, submission_id, user)
    if submission.lexical_state is not None:
        # The saved state is authoritative; re-seeding would discard edits.
        return {
            "html": None,
            "status": "unavailable",
            "reason": "This submission already has a saved working document.",
        }

    result = await run_in_threadpool(lexical_document_service.import_html, submission)
    return result._asdict()


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
    submission = get_visible_submission(db, submission_id, user)
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


def _current_revision_number(db: Session, submission_id) -> int:
    """The submission's head revision, or 0 when it has never been edited.

    This is the value a client echoes back as `expected_revision`, so 0 is
    meaningful rather than a null: it says "I read a document nobody had
    edited", which is a claim the server can check like any other.
    """
    existing = (
        db.query(SubmissionRevision)
        .filter(SubmissionRevision.submission_id == submission_id)
        .all()
    )
    return max((r.revision_number for r in existing), default=0)


def _next_revision_number(db: Session, submission_id) -> int:
    """1-based, per submission. Mirrors run_tracker.open_run's count+1 idiom —
    no extra app-level locking; UNIQUE(submission_id, revision_number) is the
    backstop against a genuine race."""
    return _current_revision_number(db, submission_id) + 1


# The constraint that decides a genuine race. Named here so the handler below
# can tell it apart from every other way this transaction could violate
# integrity, rather than reporting all of them as a reviewer conflict.
_REVISION_NUMBER_CONSTRAINT = "uq_submission_revisions_submission_number"
_UNIQUE_VIOLATION = "23505"  # SQLSTATE


def _is_duplicate_revision_number(exc: IntegrityError) -> bool:
    """True only for UNIQUE(submission_id, revision_number).

    Prefers the driver's structured diagnostics: psycopg2 reports both the
    SQLSTATE and the violated constraint by name, which is exact and needs no
    guessing at message wording. Only when the driver offers neither does this
    look for the constraint in the text — and then for that specific name, not
    an arbitrary fragment like "duplicate key".

    Anything else — a deleted user breaking `created_by`, an audit row failing
    its own constraints — is NOT this race, and must not be dressed up as one:
    telling a reviewer another reviewer changed the document, when the real
    fault is a broken foreign key, sends them to a "Keep my version" button
    that can never succeed.
    """
    orig = getattr(exc, "orig", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint is not None:
        return constraint == _REVISION_NUMBER_CONSTRAINT
    sqlstate = getattr(orig, "pgcode", None)
    if sqlstate is not None and sqlstate != _UNIQUE_VIOLATION:
        return False
    return _REVISION_NUMBER_CONSTRAINT in str(orig if orig is not None else exc)


def _conflict(expected: Optional[int], current: int) -> HTTPException:
    """409 for a write built on a revision that is no longer current.

    A structured detail rather than this route's usual sentence, because the
    editor has to act on it — stop autosaving, keep the reviewer's local text,
    and offer the newer revision — and cannot do that by parsing prose. The
    `message` key keeps it readable wherever an error is shown as-is.

    `saved: False` is the load-bearing field. It states plainly that nothing
    was written, so the client knows the reviewer's work exists only in their
    browser and must not be discarded.
    """
    return HTTPException(
        status_code=409,
        detail={
            "error": "revision_conflict",
            "message": (
                "This document was changed by someone else while you were editing. "
                "Your work has not been saved and is still in your editor."
            ),
            "expected_revision": expected,
            "current_revision": current,
            "saved": False,
        },
    )


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
    submission = get_visible_submission(db, submission_id, user)

    # Optimistic concurrency. Read the head once and use it for both the check
    # and the allocation, so the number written is the one that was verified.
    head = _current_revision_number(db, submission.id)
    if body.expected_revision is not None and body.expected_revision != head:
        raise _conflict(body.expected_revision, head)

    revision = SubmissionRevision(
        submission_id=submission.id,
        revision_number=head + 1,
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

    # record_sync, not the async record: the event rides this transaction, so
    # an edit can never commit without the trail row that attributes it.
    # `revision_number` is what trail_service uses to resolve the before/after.
    audit.record_sync(
        db, "submission_edited", actor=user,
        target_type="submission", target_id=str(submission.id),
        scope_submission_id=submission.id,
        metadata={
            "revision_number": revision.revision_number,
            "source": revision.source,
            "note": revision.note,
            "applied_violation_ids": [str(v) for v in (body.applied_violation_ids or [])],
        },
    )

    try:
        db.commit()
    except IntegrityError as exc:
        # Roll back first either way: the transaction is dead once the database
        # has rejected it, and revision, current_content, the working document,
        # fix_applied flags and the audit row all die together because they
        # share this one commit.
        db.rollback()
        if not _is_duplicate_revision_number(exc):
            # Not the race. Let it surface as the server error it actually is
            # rather than blaming an imaginary second reviewer.
            raise
        # Two writers passed the check above against the same head and raced to
        # insert the same revision_number, and UNIQUE(submission_id,
        # revision_number) decided between them. This is why the constraint
        # stays a backstop rather than the mechanism: it turns an
        # unserialisable race into the same 409 the ordinary stale write
        # already gets, so the client has one thing to handle instead of two.
        raise _conflict(body.expected_revision, _current_revision_number(db, submission.id))

    db.refresh(revision)
    return _serialize_revision(revision)


@router.get("/{submission_id}/revisions")
async def list_revisions(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """List every revision for a submission, oldest first."""
    submission = get_visible_submission(db, submission_id, user)

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
    submission = get_visible_submission(db, submission_id, user)
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
    audit.record_sync(
        db, "comment_created", actor=user,
        target_type="comment", target_id=str(comment.id),
        scope_submission_id=submission.id,
        after={"body": comment.body, "anchor_text": comment.anchor_text},
    )
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
    submission = get_visible_submission(db, submission_id, user)

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

    # Captured before mutating — the point of a before/after pair is that the
    # "before" is the value someone actually replaced.
    previous = {"body": comment.body, "resolved": comment.resolved}
    if body.body is not None:
        comment.body = body.body
    if body.resolved is not None:
        comment.resolved = body.resolved
    audit.record_sync(
        db, "comment_updated", actor=user,
        target_type="comment", target_id=str(comment.id),
        scope_submission_id=comment.submission_id,
        before=previous,
        after={"body": comment.body, "resolved": comment.resolved},
    )
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
        audit.record_sync(
            db, "comment_deleted", actor=user,
            target_type="comment", target_id=str(comment_id),
            scope_submission_id=comment.submission_id,
            before={"body": comment.body, "resolved": comment.resolved},
        )
        db.delete(comment)
        db.commit()
    return {"message": "Comment deleted", "id": comment_id}


@router.get("/{submission_id}/draft-diff")
async def submission_draft_diff(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """The reviewer's corrections as a redline: uploaded original vs working copy.

    Split view asks "what did we change, and is the new draft better?" — a
    question about the two drafts, not about the compliance findings. It is the
    same question Compare answers between two files, so it runs the same
    aligner (`comparison_service.build_diff`) rather than a second one that
    could disagree with it.

    Both sides must come through the SAME reader, or the redline is fiction.
    The working copy is the Lexical editor's text, and the editor was seeded by
    `lexical_document_service.import_text` — so that is the baseline, not
    `original_content`, which is the analyser's own extraction of the same
    upload. The two disagree by design (the analyser labels page headers and
    footers, joins table cells with pipes and keeps PDF page chrome), and
    diffing across them reported hundreds of extractor disagreements as
    reviewer edits the moment anyone saved.

    A submission with no importable upload — pasted text, HTML, markdown — has
    no editor lineage to be seeded from, so `original_content` IS its baseline
    and stays the answer there. Same fallback when the conversion fails, since
    the editor degrades to extracted text in that case too.

    The conversion runs off the event loop: it is seconds of CPU on a long
    DOCX, and this handler is async (see GET /import-html for the same reason).
    """
    submission = get_visible_submission(db, submission_id, user)

    baseline = await run_in_threadpool(lexical_document_service.import_text, submission)
    original = baseline if baseline is not None else (submission.original_content or "")
    working = submission.current_content or original
    blocks = comparison_service.build_diff(
        comparison_service.split_text_paragraphs(original),
        comparison_service.split_text_paragraphs(working),
    )
    changed = sum(1 for b in blocks if b.get("type") != "equal")
    return {
        "blocks": blocks,
        "changed": changed,
        # An unedited document is a valid answer, and the pane says so rather
        # than rendering an empty redline that reads as a failure. Read off the
        # blocks, not off string equality: the two projections of one unedited
        # document can differ in whitespace, and whitespace is not an edit.
        "edited": bool(submission.current_content) and changed > 0,
    }


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


# Kinds that put the document and the findings in ONE artifact. Only these can
# misrepresent a corrected document as carrying the previous version's findings,
# so only these are refused while the findings are stale.
#
# `clean.*` is the corrected document and cites no finding at all;
# `feedback-report.*` is the log of what reviewers did, which an edit does not
# invalidate. Refusing those too meant that editing a document — the entire
# point of the editor — left the reviewer unable to download the very document
# they had just corrected, with no way round it but a full re-analysis.
_STALE_BLOCKED_EXPORTS = {
    "annotated.docx",
    "annotated.pdf",
    "report.docx",
    "report.pdf",
    "bundle.zip",  # contains both of the above
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
    submission = get_visible_submission(db, submission_id, user)

    # An export must never pair a corrected document with the findings of the
    # version before the correction. Enforced here, not only in the UI banner,
    # because the export URL is directly reachable — but only for the kinds
    # that actually combine the two (see _STALE_BLOCKED_EXPORTS).
    if kind in _STALE_BLOCKED_EXPORTS and export_common.findings_are_stale(db, submission.id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Document edited since the last analysis — re-run the compliance check "
                "before exporting an annotated copy or a findings report. The clean copy "
                "and the reviewer feedback report are unaffected and still download."
            ),
        )

    try:
        data = submission_export_service.build_export(db, submission, kind)
    except GotenbergError as e:
        raise HTTPException(status_code=502, detail=f"PDF conversion unavailable: {e}")

    filename = f"{_safe_title(submission.title)}-{kind}"

    # An export is disclosure — worth recording, but not worth failing the
    # download over, so this takes the best-effort path rather than record_sync.
    asyncio.create_task(audit.record(
        "export_generated", actor=user,
        target_type="submission", target_id=str(submission.id),
        scope_submission_id=submission.id,
        metadata={"kind": kind, "bytes": len(data)},
    ))

    return Response(
        content=data,
        media_type=_EXPORT_MEDIA[kind],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Approval — the sign-off the workflow ended without.
#
# `approval_status` was read by two responses and written by no route, so
# "approved" was a value nobody could set. Approval is only worth recording if
# it is hard to record carelessly, so every gate below refuses with 409 and
# says which one refused. Only the criticals gate is overridable, and only
# with a reason that goes into the audit trail.
# ---------------------------------------------------------------------------

# A degraded ('needs_review') or hard-failed run persists NO check, so an
# OLDER check survives on the submission. Approving on it would sign off
# findings the newest attempt could not reproduce.
NON_GRADEABLE_STATUSES = {"needs_review", "failed"}

# Quoted from the product design, and from POST /analyze/{id}/scoped's own
# docstring — the scoped route deliberately writes NULL score/grade.
SCOPED_RUN_REFUSAL = (
    "A partial re-run cannot produce a document score, and does not pretend to. "
    "Approval requires a whole-document run."
)


class ApprovalRequest(BaseModel):
    # Required only when unresolved critical findings remain. Recorded verbatim
    # in the audit event — an override with no stated reason is not an override,
    # it is just an approval with the check switched off.
    override_reason: Optional[str] = None


def _latest_run_is_scoped(db: Session, check) -> bool:
    """Was the run that produced this check a partial one? Same lookup
    POST /analyze/{id}/scoped uses to stamp `scoped: true` on it."""
    run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.compliance_check_id == check.id)
        .order_by(AnalysisRun.run_number.desc())
        .first()
    )
    return bool((getattr(run, "run_metadata", None) or {}).get("scoped"))


def _unresolved_criticals(db: Session, check) -> list:
    """Critical findings nobody has answered: live (not suppressed below the
    confidence floor), model-authored (a reviewer's own flag is not something
    the model is asking the reviewer to resolve), and with no verdict on it."""
    return [
        v for v in export_common.check_violations(db, check)
        if export_common.normalize_severity(v.severity) == "critical"
        and not v.suppressed
        and (v.source or "model") == "model"
        and v.review_status is None
    ]


def _approval_gate(db: Session, submission: Submission) -> dict:
    """Everything both approval routes need: the check being approved, every
    blocker standing in the way, and the criticals an override can clear.

    Order is deliberate — POST /approve reports the first blocker, and "there
    is nothing to approve" has to be said before "what you would approve is
    only part of the document".
    """
    check = export_common.latest_check(db, submission.id)
    blockers = []

    if check is None or (check.status or "completed") != "completed":
        blockers.append((
            "no_analysis", False,
            "There is no completed analysis to approve — run the compliance "
            "check on this submission first",
        ))
    if (submission.status or "") in NON_GRADEABLE_STATUSES:
        blockers.append((
            "not_gradeable", False,
            f"Submission is '{submission.status}' — the last analysis could not "
            "be graded, so there is no result to sign off",
        ))
    if export_common.findings_are_stale(db, submission.id):
        blockers.append((
            "stale_findings", False,
            "Document edited since the last analysis — re-run the compliance "
            "check before approving",
        ))
    if check is not None and _latest_run_is_scoped(db, check):
        blockers.append(("scoped_run", False, SCOPED_RUN_REFUSAL))

    criticals = _unresolved_criticals(db, check)
    if criticals:
        blockers.append((
            "unresolved_criticals", True,
            f"{len(criticals)} unresolved critical finding(s) — approving with "
            "criticals outstanding requires an explicit override_reason",
        ))

    return {
        "check": check,
        "criticals": criticals,
        "blockers": [
            {"code": code, "overridable": overridable, "message": message}
            for code, overridable, message in blockers
        ],
    }


@router.post("/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    body: ApprovalRequest = Body(default=ApprovalRequest()),
    # submission:approve, not submission:create — every role holds the latter,
    # which let the reviewer who edited a document also sign it off. Sign-off
    # is admin-and-above so the two are always different people (spec D4).
    user: dict = Depends(require("submission:approve")),
    db: Session = Depends(get_db),
):
    """Record a human sign-off on the current document + its current findings.

    Refuses (409) a stale document, a partial run, an ungradeable submission,
    and unresolved criticals — the last of which an `override_reason` can
    clear, and nothing else can.
    """
    submission = get_visible_submission(db, submission_id, user)

    gate = _approval_gate(db, submission)
    reason = ((body.override_reason if body else None) or "").strip()

    for blocker in gate["blockers"]:
        if blocker["overridable"] and reason:
            continue
        raise HTTPException(status_code=409, detail=blocker["message"])

    previous = submission.approval_status
    submission.approval_status = "approved"

    # Sign-off ends the review that produced the document. Without this the
    # assignment stayed active forever: the reviewer's bucket never drained and
    # the partial unique index kept the document locked to work nobody could
    # finish. Same transaction as the approval on purpose — the two records must
    # not be able to disagree, and `close` cannot fail here (the outcome is a
    # known one, and `active_for_submission` only returns closeable states).
    active = assignment_service.active_for_submission(db, submission.id)
    if active is not None:
        assignment_service.close(
            db, assignment=active, actor=user, outcome="approved",
            note=f"Closed by approval of {submission.id}.",
        )
    db.commit()

    closed_assignment_id = str(active.id) if active is not None else None
    check_id = str(gate["check"].id) if gate["check"] else None
    approver = str(user.id) if getattr(user, "id", None) else None
    approved_at = datetime.now(timezone.utc).isoformat()

    # `submissions` has no approved_by/approved_at column and this feature ships
    # no migration (two are already unapplied on the deployed system), so the
    # audit row IS the record of who signed off and when. Awaited, not
    # fire-and-forget: an approval whose audit event silently never ran is an
    # approval nobody can defend.
    from app.services.observability import audit
    await audit.record(
        "submission_approval_override" if reason else "submission_approved",
        actor=user,
        target_type="submission",
        target_id=str(submission.id),
        # Without this the sign-off is absent from the document's own trail,
        # which filters on scope_submission_id alone — leaving the one entry a
        # compliance reviewer most needs to find out of the record.
        scope_submission_id=submission.id,
        before={"approval_status": previous},
        after={"approval_status": "approved"},
        metadata={
            "check_id": check_id,
            "approved_by": approver,
            "approved_at": approved_at,
            "override_reason": reason or None,
            "unresolved_critical_count": len(gate["criticals"]),
            "closed_assignment_id": closed_assignment_id,
        },
    )

    return {
        "id": str(submission.id),
        "approval_status": submission.approval_status,
        "check_id": check_id,
        "approved_by": approver,
        "approved_at": approved_at,
        "overridden": bool(reason),
        "override_reason": reason or None,
        "unresolved_critical_count": len(gate["criticals"]),
    }


@router.get("/{submission_id}/approval")
async def get_approval_state(
    submission_id: str,
    user: dict = Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """Whether approval is possible right now and, if not, exactly why — so the
    UI can say what to fix instead of just greying the button out.

    `can_approve` answers "would a plain POST /approve succeed"; when the only
    blocker left is unresolved criticals, `requires_override` says so and the
    button becomes "approve with a reason" rather than disabled.
    """
    submission = get_visible_submission(db, submission_id, user)

    gate = _approval_gate(db, submission)
    blockers = gate["blockers"]
    return {
        "id": str(submission.id),
        "approval_status": submission.approval_status,
        "can_approve": not blockers,
        "requires_override": bool(blockers) and all(b["overridable"] for b in blockers),
        "blockers": blockers,
        "unresolved_critical_count": len(gate["criticals"]),
        "check_id": str(gate["check"].id) if gate["check"] else None,
    }


@router.delete("/{submission_id}")
async def delete_submission(
    submission_id: str,
    user: dict = Depends(require("submission:delete")),
    db: Session = Depends(get_db)
):
    """Delete a submission."""
    submission = get_visible_submission(db, submission_id, user)

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
    asyncio.create_task(audit.record("submission_deleted", actor=user, target_type="submission", target_id=submission_id, scope_submission_id=submission_id))

    return {"message": "Submission deleted", "id": submission_id}
