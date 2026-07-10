"""Super-Admin console API (audit-trail 05 §6).

Thin, defensive read handlers over the observability ledger
(``llm_usage_events`` + ``analysis_runs`` + ``user_sessions`` + ``audit_events``)
plus user provisioning. Every route is gated by a *specific* permission via
``Depends(require(...))``:

- user management  → ``users:manage``   (admin + super_admin; D3 enforced in-handler)
- usage / cost     → ``usage:view``     (super_admin only)
- audit feeds      → ``audit:view``     (super_admin only)

All DB access is synchronous (the app uses sync SQLAlchemy); rollups use
``sqlalchemy.func`` (sum/count/coalesce/max/date_trunc) and every money sum is
wrapped in ``func.coalesce(..., 0)`` so a range with no rows returns 0, not NULL.
Audit writes are best-effort (never raise). The console never grades documents —
``super_admin`` is simply absent from ``analysis:run``/``chat:use``.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import require
from app.auth.passwords import hash_password
from app.auth.sessions import revoke_all_for_user
from app.services.observability import audit
from app.models.user import User
from app.models.user_session import UserSession
from app.models.analysis_run import AnalysisRun
from app.models.llm_usage_event import LlmUsageEvent
from app.models.audit_event import AuditEvent
from app.models.submission import Submission

router = APIRouter(prefix="/super_admin", tags=["Super Admin"])


# --- small, defensive coercers --------------------------------------------

def _i(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _iso(dt) -> Optional[str]:
    try:
        return dt.isoformat() if dt is not None else None
    except Exception:  # noqa: BLE001 - never let formatting break a read
        return None


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(int(days or 0), 0))


# --- request bodies --------------------------------------------------------

class CreateUserIn(BaseModel):
    username: str
    password: str
    registered_ip: Optional[str] = None
    role: str = "user"


class UpdateUserIn(BaseModel):
    registered_ip: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    reset_password: Optional[bool] = None
    new_password: Optional[str] = None


# ===========================================================================
# Users  (users:manage)
# ===========================================================================

@router.get("/users")
async def list_users(db: Session = Depends(get_db), _actor=Depends(require("users:manage"))):
    """User roster + status + lifetime run count + lifetime LLM spend."""
    users = db.query(User).order_by(User.created_at.desc()).all()

    # #runs per user (count analysis_runs.triggered_by)
    run_rows = (
        db.query(
            AnalysisRun.triggered_by.label("uid"),
            func.count(AnalysisRun.id).label("runs"),
        )
        .group_by(AnalysisRun.triggered_by)
        .all()
    )
    runs_by_user = {str(r.uid): _i(r.runs) for r in run_rows if r.uid is not None}

    # total_cost per user (sum llm_usage_events)
    cost_rows = (
        db.query(
            LlmUsageEvent.user_id.label("uid"),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).label("cost"),
        )
        .group_by(LlmUsageEvent.user_id)
        .all()
    )
    cost_by_user = {str(r.uid): _f(r.cost) for r in cost_rows if r.uid is not None}

    out = []
    for u in users:
        uid = str(u.id)
        out.append(
            {
                "id": uid,
                "username": u.username,
                "role": u.role,
                "registered_ip": u.registered_ip,
                "is_active": bool(u.is_active),
                "last_login_at": _iso(u.last_login_at),
                "must_change_password": bool(u.must_change_password),
                "runs": runs_by_user.get(uid, 0),
                "total_cost_usd": cost_by_user.get(uid, 0.0),
            }
        )
    return {"users": out}


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserIn,
    request: Request,
    db: Session = Depends(get_db),
    actor=Depends(require("users:manage")),
):
    """Provision a new account with a temp password (must be changed on first
    login). Decision D3: an ``admin`` actor may create ONLY ``user`` accounts; a
    ``super_admin`` may create any role."""
    role = (body.role or "user").strip()
    if role not in {"user", "admin", "super_admin"}:
        raise HTTPException(status_code=400, detail="Unknown role.")

    # Decision D3 — admins provision graders only.
    if getattr(actor, "role", None) == "admin" and role != "user":
        raise HTTPException(status_code=403, detail="Admins may only create user accounts.")

    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username and password are required.")

    existing = db.query(User).filter(User.username == body.username).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists.")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),  # plaintext is never stored
        registered_ip=body.registered_ip,
        role=role,
        must_change_password=True,
        is_active=True,
        created_by=getattr(actor, "id", None),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # after = {username, role, ip} — NEVER the password.
    await audit.record(
        "user_created",
        actor=actor,
        request=request,
        target_type="user",
        target_id=str(getattr(user, "id", None)),
        after={"username": user.username, "role": user.role, "ip": user.registered_ip},
    )
    return {
        "id": str(getattr(user, "id", None)),
        "username": user.username,
        "role": user.role,
        "must_change_password": True,
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserIn,
    request: Request,
    db: Session = Depends(get_db),
    actor=Depends(require("users:manage")),
):
    """Update ip / role / is_active, or reset the password. Each change emits its
    own audit event with before/after; disabling or resetting revokes all
    sessions for the target user."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    # D3 — an admin actor may only manage grader (``user``) accounts; only a
    # super_admin may touch admin / super_admin rows.
    if getattr(actor, "role", None) == "admin" and getattr(user, "role", None) != "user":
        raise HTTPException(status_code=403, detail="Admins may only manage user accounts.")

    events: list = []  # (event_type, before, after) tuples, emitted after commit

    # --- registered_ip -----------------------------------------------------
    if body.registered_ip is not None and body.registered_ip != user.registered_ip:
        before_ip = user.registered_ip
        user.registered_ip = body.registered_ip
        events.append(("user_ip_updated", {"registered_ip": before_ip},
                       {"registered_ip": body.registered_ip}))

    # --- role --------------------------------------------------------------
    if body.role is not None and body.role != user.role:
        if body.role not in {"user", "admin", "super_admin"}:
            raise HTTPException(status_code=400, detail="Unknown role.")
        # Decision D3 — admins may not mint elevated roles.
        if getattr(actor, "role", None) == "admin" and body.role != "user":
            raise HTTPException(status_code=403, detail="Admins may only assign the user role.")
        before_role = user.role
        user.role = body.role
        events.append(("user_role_changed", {"role": before_role}, {"role": body.role}))

    # --- is_active (enable / disable) --------------------------------------
    disabled_now = False
    if body.is_active is not None and bool(body.is_active) != bool(user.is_active):
        before_active = bool(user.is_active)
        user.is_active = bool(body.is_active)
        if user.is_active:
            events.append(("user_enabled", {"is_active": before_active}, {"is_active": True}))
        else:
            disabled_now = True
            events.append(("user_disabled", {"is_active": before_active}, {"is_active": False}))

    # --- password reset ----------------------------------------------------
    reset_now = False
    if body.reset_password:
        if not body.new_password:
            raise HTTPException(status_code=400, detail="new_password is required to reset.")
        user.password_hash = hash_password(body.new_password)  # temp; hashed, never stored raw
        user.must_change_password = True
        user.password_updated_at = func.now()
        reset_now = True
        events.append(("user_password_reset", None, {"must_change_password": True}))

    db.commit()

    # Kill live sessions AFTER the state change is committed.
    if disabled_now or reset_now:
        await revoke_all_for_user(str(user_id))

    for event_type, before, after in events:
        await audit.record(
            event_type,
            actor=actor,
            request=request,
            target_type="user",
            target_id=str(user_id),
            before=before,
            after=after,
        )

    return {
        "id": str(user_id),
        "username": user.username,
        "role": user.role,
        "is_active": bool(user.is_active),
        "must_change_password": bool(user.must_change_password),
    }


@router.post("/users/{user_id}/force-logout")
async def force_logout(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor=Depends(require("users:manage")),
):
    """Revoke every active session for a user (leaves the account enabled)."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    # D3 — admins may only manage grader (``user``) accounts.
    if getattr(actor, "role", None) == "admin" and getattr(target, "role", None) != "user":
        raise HTTPException(status_code=403, detail="Admins may only manage user accounts.")
    await revoke_all_for_user(str(user_id))
    await audit.record(
        "user_force_logout",
        actor=actor,
        request=request,
        target_type="user",
        target_id=str(user_id),
    )
    return {"ok": True, "user_id": str(user_id)}


# ===========================================================================
# Usage / cost  (usage:view) — mirrors 02 §7 GROUP-BY rollups
# ===========================================================================

@router.get("/usage/summary")
async def usage_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """Per-user tokens + cost over the window ("who is spending money")."""
    since = _since(days)
    rows = (
        db.query(
            User.id.label("uid"),
            User.username.label("username"),
            User.role.label("role"),
            func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).label("cost_usd"),
            func.count(func.distinct(LlmUsageEvent.run_id)).label("runs"),
        )
        .join(LlmUsageEvent, LlmUsageEvent.user_id == User.id)
        .filter(LlmUsageEvent.created_at >= since)
        .group_by(User.id, User.username, User.role)
        .order_by(func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).desc())
        .all()
    )
    return {
        "days": days,
        "users": [
            {
                "user_id": str(r.uid),
                "username": r.username,
                "role": r.role,
                "input_tokens": _i(r.input_tokens),
                "output_tokens": _i(r.output_tokens),
                "total_cost_usd": _f(r.cost_usd),
                "runs": _i(r.runs),
            }
            for r in rows
        ],
    }


@router.get("/usage/by-document")
async def usage_by_document(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """Per-submission input/output tokens + cost + #re-runs + last run."""
    since = _since(days)
    rows = (
        db.query(
            Submission.id.label("sid"),
            Submission.title.label("title"),
            User.username.label("graded_by"),
            func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).label("cost_usd"),
            func.max(AnalysisRun.run_number).label("total_runs"),
            func.max(AnalysisRun.started_at).label("last_run"),
        )
        .join(AnalysisRun, AnalysisRun.submission_id == Submission.id)
        .outerjoin(LlmUsageEvent, LlmUsageEvent.run_id == AnalysisRun.id)
        .outerjoin(User, User.id == AnalysisRun.triggered_by)
        .filter(AnalysisRun.started_at >= since)
        .group_by(Submission.id, Submission.title, User.username)
        .order_by(func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).desc())
        .all()
    )
    return {
        "days": days,
        "documents": [
            {
                "submission_id": str(r.sid),
                "title": r.title,
                "graded_by": r.graded_by,
                "input_tokens": _i(r.input_tokens),
                "output_tokens": _i(r.output_tokens),
                "total_cost_usd": _f(r.cost_usd),
                "total_runs": _i(r.total_runs),
                "last_run": _iso(r.last_run),
            }
            for r in rows
        ],
    }


@router.get("/usage/timeseries")
async def usage_timeseries(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """Daily total tokens + cost across all users."""
    since = _since(days)
    day = func.date_trunc("day", LlmUsageEvent.created_at).label("day")
    rows = (
        db.query(
            day,
            func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).label("cost_usd"),
        )
        .filter(LlmUsageEvent.created_at >= since)
        .group_by(day)
        .order_by(day)
        .all()
    )
    return {
        "days": days,
        "points": [
            {"day": _iso(r.day), "total_tokens": _i(r.tokens), "total_cost_usd": _f(r.cost_usd)}
            for r in rows
        ],
    }


@router.get("/runs")
async def list_runs(
    user: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """analysis_runs list (filterable by user / status / window)."""
    since = _since(days)
    q = db.query(AnalysisRun).filter(AnalysisRun.started_at >= since)
    if user:
        q = q.filter(AnalysisRun.triggered_by == user)
    if status:
        q = q.filter(AnalysisRun.status == status)
    rows = q.order_by(AnalysisRun.started_at.desc()).all()
    return {"runs": [_run_dict(r) for r in rows]}


@router.get("/submissions/{submission_id}/runs")
async def submission_runs(
    submission_id: str,
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """Per-document re-run detail, ordered by run_number."""
    rows = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.submission_id == submission_id)
        .order_by(AnalysisRun.run_number)
        .all()
    )
    return {"submission_id": str(submission_id), "runs": [_run_dict(r) for r in rows]}


def _run_dict(r) -> dict:
    return {
        "id": str(getattr(r, "id", None)),
        "submission_id": str(getattr(r, "submission_id", None)),
        "triggered_by": str(r.triggered_by) if getattr(r, "triggered_by", None) else None,
        "run_number": _i(getattr(r, "run_number", 0)),
        "is_rerun": bool(getattr(r, "is_rerun", False)),
        "trigger_source": getattr(r, "trigger_source", None),
        "status": getattr(r, "status", None),
        "degraded_reason": getattr(r, "degraded_reason", None),
        "duration_ms": (None if getattr(r, "duration_ms", None) is None else _i(r.duration_ms)),
        "prompt_tokens": _i(getattr(r, "prompt_tokens", 0)),
        "completion_tokens": _i(getattr(r, "completion_tokens", 0)),
        "total_tokens": _i(getattr(r, "total_tokens", 0)),
        "total_cost_usd": _f(getattr(r, "total_cost_usd", 0)),
        "started_at": _iso(getattr(r, "started_at", None)),
        "finished_at": _iso(getattr(r, "finished_at", None)),
    }


@router.get("/sessions")
async def list_sessions(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("usage:view")),
):
    """Login sessions (active + historical) with per-session duration, plus a
    per-user active-time rollup."""
    since = _since(days)
    rows = (
        db.query(
            UserSession.id.label("id"),
            UserSession.user_id.label("user_id"),
            User.username.label("username"),
            UserSession.ip.label("ip"),
            UserSession.login_at.label("login_at"),
            UserSession.last_seen_at.label("last_seen_at"),
            UserSession.logout_at.label("logout_at"),
            UserSession.duration_seconds.label("duration_seconds"),
            UserSession.status.label("status"),
        )
        .join(User, User.id == UserSession.user_id)
        .filter(UserSession.login_at >= since)
        .order_by(UserSession.login_at.desc())
        .all()
    )

    def _duration(r) -> Optional[int]:
        if getattr(r, "duration_seconds", None) is not None:
            return _i(r.duration_seconds)
        login = getattr(r, "login_at", None)
        end = getattr(r, "logout_at", None) or getattr(r, "last_seen_at", None)
        if login is not None and end is not None:
            try:
                return max(int((end - login).total_seconds()), 0)
            except Exception:  # noqa: BLE001
                return None
        return None

    sessions = [
        {
            "id": r.id,
            "user_id": str(r.user_id) if r.user_id is not None else None,
            "username": r.username,
            "ip": r.ip,
            "login_at": _iso(r.login_at),
            "last_seen_at": _iso(r.last_seen_at),
            "logout_at": _iso(r.logout_at),
            "duration_seconds": _duration(r),
            "status": r.status,
        }
        for r in rows
    ]

    # Per-user active time (last N days).
    active_rows = (
        db.query(
            User.username.label("username"),
            func.count(UserSession.id).label("sessions"),
            func.coalesce(func.sum(func.coalesce(UserSession.duration_seconds, 0)), 0).label(
                "active_seconds"
            ),
        )
        .join(User, User.id == UserSession.user_id)
        .filter(UserSession.login_at >= since)
        .group_by(User.username)
        .all()
    )
    per_user = [
        {
            "username": r.username,
            "sessions": _i(r.sessions),
            "active_seconds": _i(r.active_seconds),
        }
        for r in active_rows
    ]

    return {"days": days, "sessions": sessions, "per_user_active_time": per_user}


@router.get("/export/usage.csv")
async def export_usage_csv(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    actor=Depends(require("usage:view")),
):
    """Stream the per-user usage rollup as CSV (text/csv)."""
    since = _since(days)
    rows = (
        db.query(
            User.username.label("username"),
            User.role.label("role"),
            func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).label("cost_usd"),
            func.count(func.distinct(LlmUsageEvent.run_id)).label("runs"),
        )
        .join(LlmUsageEvent, LlmUsageEvent.user_id == User.id)
        .filter(LlmUsageEvent.created_at >= since)
        .group_by(User.id, User.username, User.role)
        .order_by(func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0).desc())
        .all()
    )

    await audit.record(
        "usage_exported",
        actor=actor,
        request=request,
        metadata={"days": days, "rows": len(rows)},
    )

    def _stream():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["username", "role", "input_tokens", "output_tokens", "total_cost_usd", "runs"]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for r in rows:
            writer.writerow(
                [
                    r.username,
                    r.role,
                    _i(r.input_tokens),
                    _i(r.output_tokens),
                    _f(r.cost_usd),
                    _i(r.runs),
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=usage.csv"},
    )


# ===========================================================================
# Audit  (audit:view)
# ===========================================================================

@router.get("/audit")
async def audit_feed(
    event_type: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("audit:view")),
):
    """Append-only audit feed, reverse-chronological, with optional filters."""
    since = _since(days)
    q = db.query(AuditEvent).filter(AuditEvent.created_at >= since)
    if event_type:
        q = q.filter(AuditEvent.event_type == event_type)
    if actor:
        q = q.filter(AuditEvent.actor_user_id == actor)
    rows = q.order_by(AuditEvent.created_at.desc()).all()
    return {"events": [_audit_dict(e) for e in rows]}


@router.get("/rules/audit")
async def rules_audit(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _actor=Depends(require("audit:view")),
):
    """Rule-change timeline: audit events whose type starts with ``rule_``."""
    since = _since(days)
    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.created_at >= since)
        .filter(AuditEvent.event_type.startswith("rule_"))
        .order_by(AuditEvent.created_at.desc())
        .all()
    )
    return {"events": [_audit_dict(e) for e in rows]}


def _audit_dict(e) -> dict:
    return {
        "id": str(getattr(e, "id", None)),
        "event_type": getattr(e, "event_type", None),
        "actor_user_id": (str(e.actor_user_id) if getattr(e, "actor_user_id", None) else None),
        "actor_role": getattr(e, "actor_role", None),
        "actor_ip": getattr(e, "actor_ip", None),
        "session_id": getattr(e, "session_id", None),
        "target_type": getattr(e, "target_type", None),
        "target_id": getattr(e, "target_id", None),
        "before": getattr(e, "before_state", None),
        "after": getattr(e, "after_state", None),
        "metadata": getattr(e, "metadata_", None),
        "created_at": _iso(getattr(e, "created_at", None)),
    }
