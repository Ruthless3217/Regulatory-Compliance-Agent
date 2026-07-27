"""Authentication routes: /auth/login, /logout, /me, /heartbeat, /change-password.

Verifies username + password + (strict) IP; issues an opaque Redis session +
httpOnly cookie; opens/closes a durable ``user_sessions`` row for session-time
reporting; and applies a per-(username,ip) login lockout. All DB access is
synchronous (the app uses sync SQLAlchemy). Audit writes are best-effort.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.sql import func

from app.config import settings
from app.database import get_db
from app.api.rate_limit import extract_client_key
from app.models.user import User
from app.models.user_session import UserSession
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, revoke_session, revoke_all_for_user
from app.auth.dependencies import get_current_user, ip_allowed_for_user

router = APIRouter(prefix="/auth", tags=["Auth"])


# --- request models --------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


# --- login lockout (in-process fixed window, keyed on username+ip) ---------
# Single-worker UAT scope; a Redis-shared counter is a Phase 7 hardening item.
_failures: dict[str, tuple[float, int]] = {}


def _lock_key(username: str, ip: str) -> str:
    return f"{username}:{ip}"


def _is_locked(key: str) -> bool:
    ts = _failures.get(key)
    if not ts:
        return False
    start, count = ts
    if time.time() - start >= settings.login_lockout_seconds:
        _failures.pop(key, None)
        return False
    return count >= settings.login_max_attempts


def _record_failure(key: str) -> None:
    start, count = _failures.get(key, (time.time(), 0))
    if time.time() - start >= settings.login_lockout_seconds:
        start, count = time.time(), 0
    _failures[key] = (start, count + 1)


def _clear_failures(key: str) -> None:
    _failures.pop(key, None)


# --- audit helper (best-effort; observability wired in a later phase) ------

async def _audit(event_type, **kw):
    try:
        from app.services.observability import audit
        await audit.record(event_type, **kw)
    except Exception:
        pass


def _client_ip(request: Request) -> str:
    return extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)


# --- routes ----------------------------------------------------------------

@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db=Depends(get_db)):
    ip = _client_ip(request)
    key = _lock_key(body.username, ip)
    if _is_locked(key):
        await _audit("login_locked", request=request, metadata={"username": body.username, "ip": ip})
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    user = db.query(User).filter(User.username == body.username).first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        _record_failure(key)
        await _audit("login_failed", actor=user, request=request, metadata={"reason": "bad_credentials"})
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    if settings.auth_ip_binding_mode == "strict" and not ip_allowed_for_user(user, ip):
        _record_failure(key)
        await _audit("login_failed", actor=user, request=request, metadata={"reason": "ip_mismatch", "ip": ip})
        raise HTTPException(
            status_code=403,
            detail="This device isn't recognised for your account. Ask an admin to update your IP.",
        )

    _clear_failures(key)
    sid = await create_session(user, ip, request.headers.get("user-agent", ""))
    db.add(UserSession(id=sid, user_id=user.id, ip=ip,
                       user_agent=request.headers.get("user-agent", "")[:400], status="active"))
    user.last_login_at = func.now()
    db.commit()
    await _audit("login", actor=user, request=request)

    response.set_cookie(
        "rca_session", sid, httponly=True, secure=settings.session_cookie_secure,
        samesite="strict", max_age=settings.session_absolute_ttl_seconds,
    )
    return {"role": user.role, "must_change_password": user.must_change_password}


@router.post("/logout")
async def logout(request: Request, response: Response, db=Depends(get_db),
                 user: User = Depends(get_current_user)):
    sid = getattr(request.state, "session_id", None)
    if sid:
        await revoke_session(sid)
        row = db.get(UserSession, sid)
        if row and row.logout_at is None:
            row.logout_at = func.now()
            if row.login_at:
                row.duration_seconds = int((datetime.now(timezone.utc) - row.login_at).total_seconds())
            row.status = "closed"
            db.commit()
    await _audit("logout", actor=user, request=request)
    response.delete_cookie("rca_session")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


@router.post("/heartbeat")
async def heartbeat(request: Request, db=Depends(get_db), user: User = Depends(get_current_user)):
    sid = getattr(request.state, "session_id", None)
    if sid:
        row = db.get(UserSession, sid)
        if row:
            row.last_seen_at = func.now()
            db.commit()
    return {"ok": True}


@router.post("/change-password")
async def change_password(body: ChangePasswordIn, request: Request, response: Response,
                          db=Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if len(body.new_password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters.")
    if body.new_password == user.username:
        raise HTTPException(status_code=400, detail="Password must not equal the username.")

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.password_updated_at = func.now()
    db.commit()
    await _audit("password_changed", actor=user, request=request, metadata={"by_self": True})

    # Rotate the session: revoke all (incl. current) and issue a fresh one.
    await revoke_all_for_user(str(user.id))
    ip = _client_ip(request)
    sid = await create_session(user, ip, request.headers.get("user-agent", ""))
    db.add(UserSession(id=sid, user_id=user.id, ip=ip,
                       user_agent=request.headers.get("user-agent", "")[:400], status="active"))
    db.commit()
    response.set_cookie(
        "rca_session", sid, httponly=True, secure=settings.session_cookie_secure,
        samesite="strict", max_age=settings.session_absolute_ttl_seconds,
    )
    return {"ok": True}
