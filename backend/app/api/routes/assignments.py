"""Review assignments — the admin's hand-off and the reviewer's bucket.

Service refusals map to HTTP here and nowhere else, so the lifecycle rules stay
in one place and the routes stay thin:

    ActiveAssignmentExists -> 409   already assigned; reassign instead
    IllegalTransition      -> 409   not an edge in the state machine
    NotAssignee            -> 403   someone else's work
    ValueError             -> 400   bad input (missing reason, unknown priority)

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 2
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.visibility import get_visible_submission
from app.database import get_db
from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.models.user import User
from app.services import assignment_service as svc
from app.services import trail_service

router = APIRouter(prefix="/assignments", tags=["Assignments"])


class AssignIn(BaseModel):
    submission_id: str
    assignee_id: str
    priority: str = "normal"
    due_at: Optional[datetime] = None
    note: Optional[str] = None


class ReassignIn(BaseModel):
    assignee_id: str
    note: Optional[str] = None


class SendBackIn(BaseModel):
    reason: str


class CancelIn(BaseModel):
    reason: Optional[str] = None


def _dict(a: ReviewAssignment) -> dict:
    return {
        "id": str(a.id),
        "submission_id": str(a.submission_id),
        "assignee_id": str(a.assignee_id),
        "assigned_by": str(a.assigned_by) if a.assigned_by else None,
        "status": a.status,
        "priority": a.priority,
        "due_at": a.due_at.isoformat() if a.due_at else None,
        "note": a.note,
        "outcome": a.outcome,
        "outcome_note": a.outcome_note,
        "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "closed_at": a.closed_at.isoformat() if a.closed_at else None,
        "superseded_by": str(a.superseded_by) if a.superseded_by else None,
    }


def _load(db, assignment_id) -> ReviewAssignment:
    a = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return a


def _run(fn, db):
    """Map service refusals onto HTTP, and commit on success."""
    try:
        result = fn()
    except svc.ActiveAssignmentExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except svc.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    except svc.NotAssignee as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


# --- literal paths first: they must not be shadowed by /{assignment_id} ---

@router.get("/my")
async def my_bucket(
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    """Everything currently on this reviewer's plate."""
    rows = []
    for status in ACTIVE_STATUSES:
        rows.extend(
            db.query(ReviewAssignment)
            .filter(
                ReviewAssignment.assignee_id == user.id,
                ReviewAssignment.status == status,
            )
            .all()
        )
    return {"assignments": [_dict(a) for a in rows], "total": len(rows)}


@router.get("/workload")
async def workload(
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    """Open counts per reviewer, for the assign picker.

    Not a separate screen: the number is only ever wanted at the moment of
    choosing an assignee. Sorted least-loaded first so the default choice
    spreads work rather than piling it on one person.
    """
    out = []
    for u in db.query(User).all():
        if u.role not in ("user", "admin"):
            continue
        open_count = 0
        for status in ACTIVE_STATUSES:
            open_count += (
                db.query(ReviewAssignment)
                .filter(
                    ReviewAssignment.assignee_id == u.id,
                    ReviewAssignment.status == status,
                )
                .count()
            )
        out.append({
            "user_id": str(u.id),
            "username": u.username or u.display_name or u.email,
            "role": u.role,
            "is_active": bool(u.is_active),
            "open_count": open_count,
        })
    out.sort(key=lambda r: r["open_count"])
    return {"reviewers": out}


@router.get("/for-submission/{submission_id}")
async def assignment_for_submission(
    submission_id: str,
    user=Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """The banner on the submission page: current holder plus prior hand-offs."""
    get_visible_submission(db, submission_id, user)
    rows = (
        db.query(ReviewAssignment)
        .filter(ReviewAssignment.submission_id == submission_id)
        .all()
    )
    active = svc.active_for_submission(db, submission_id)
    return {
        "active": _dict(active) if active else None,
        "history": [_dict(a) for a in rows],
    }


@router.get("/trail/document/{submission_id}")
async def document_trail_route(
    submission_id: str,
    user=Depends(require("trail:view")),
    db: Session = Depends(get_db),
):
    get_visible_submission(db, submission_id, user)
    return {"trail": trail_service.document_trail(db, submission_id)}


@router.get("/trail/reviewer/{user_id}")
async def reviewer_trail_route(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(require("trail:view")),
    db: Session = Depends(get_db),
):
    return trail_service.reviewer_trail(db, user_id, limit=limit)


# --- collection + lifecycle ---

@router.post("", status_code=201)
async def create_assignment(
    body: AssignIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    # Visibility first: an admin-only route still must not become a way to
    # confirm a document exists.
    get_visible_submission(db, body.submission_id, user)
    a = _run(lambda: svc.assign(
        db, submission_id=body.submission_id, assignee_id=body.assignee_id,
        actor=user, priority=body.priority, due_at=body.due_at, note=body.note,
    ), db)
    return _dict(a)


@router.get("")
async def list_assignments(
    status: Optional[str] = Query(default=None),
    assignee_id: Optional[str] = Query(default=None),
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    q = db.query(ReviewAssignment)
    if status:
        q = q.filter(ReviewAssignment.status == status)
    if assignee_id:
        q = q.filter(ReviewAssignment.assignee_id == assignee_id)
    rows = q.all()
    return {"assignments": [_dict(a) for a in rows], "total": len(rows)}


@router.post("/{assignment_id}/reassign")
async def reassign_assignment(
    assignment_id: str,
    body: ReassignIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    replacement = _run(lambda: svc.reassign(
        db, assignment=a, new_assignee_id=body.assignee_id, actor=user, note=body.note,
    ), db)
    return _dict(replacement)


@router.post("/{assignment_id}/start")
async def start_assignment(
    assignment_id: str,
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(lambda: svc.start(db, assignment=a, actor=user), db))


@router.post("/{assignment_id}/complete")
async def complete_assignment(
    assignment_id: str,
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(lambda: svc.complete(db, assignment=a, actor=user), db))


@router.post("/{assignment_id}/send-back")
async def send_back_assignment(
    assignment_id: str,
    body: SendBackIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(
        lambda: svc.send_back(db, assignment=a, actor=user, reason=body.reason), db))


@router.post("/{assignment_id}/cancel")
async def cancel_assignment(
    assignment_id: str,
    body: CancelIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(
        lambda: svc.cancel(db, assignment=a, actor=user, reason=body.reason), db))
