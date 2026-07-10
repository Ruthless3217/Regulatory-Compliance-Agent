from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.sql import func
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.user_session import UserSession
from app.auth.passwords import verify_password, hash_password
from app.auth.sessions import create_session, revoke_session, revoke_all_for_user, SESSION_TTL
from app.auth.dependencies import get_current_user, ip_allowed
from app.api.rate_limit import extract_client_key
from app.config import settings
from app.services.observability import audit
import time

router = APIRouter(prefix="/auth", tags=["Auth"])

# --- In-process brute-force lockout ---------------------------------------
# Per (username, client-ip): after ``settings.login_max_attempts`` failed logins
# inside a ``settings.login_lockout_seconds`` window, further attempts get 429
# until the window elapses. In-process (per worker); fail-open on any error.
_failed_logins: dict[str, list[float]] = {}


def _lockout_key(username: str, ip: str) -> str:
    return f"{username or '?'}::{ip or '?'}"


def _is_locked(key: str) -> bool:
    try:
        now = time.time()
        window = settings.login_lockout_seconds
        attempts = [t for t in _failed_logins.get(key, []) if now - t < window]
        _failed_logins[key] = attempts
        return len(attempts) >= settings.login_max_attempts
    except Exception:
        return False


def _record_failed_login(key: str) -> None:
    _failed_logins.setdefault(key, []).append(time.time())


def _clear_failed_logins(key: str) -> None:
    _failed_logins.pop(key, None)

class LoginIn(BaseModel):
    username: str
    password: str

class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str

async def open_user_session_row(db, sid: str, user: User, ip: str, request: Request):
    user_agent = request.headers.get("user-agent", "")
    session_row = UserSession(
        id=sid,
        user_id=user.id,
        ip=ip,
        user_agent=user_agent
    )
    db.add(session_row)
    db.commit()

@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db = Depends(get_db)):
    result = db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
    lock_key = _lockout_key(body.username, ip)

    if _is_locked(lock_key):
        await audit.record("login_locked", actor=user, request=request, metadata={"username": body.username})
        raise HTTPException(429, "Too many failed attempts. Please try again in a few minutes.")

    if not user or not user.is_active or not verify_password(body.password, user.password_hash or ""):
        _record_failed_login(lock_key)
        await audit.record("login_failed", actor=user, request=request, metadata={"reason": "bad_credentials"})
        raise HTTPException(401, "Invalid username or password.")

    if settings.auth_ip_binding_mode == "strict" and not ip_allowed(user, ip):
        _record_failed_login(lock_key)
        await audit.record("login_failed", actor=user, request=request, metadata={"reason": "ip_mismatch", "ip": ip})
        raise HTTPException(403, "This device isn't recognised for your account. Ask an admin to update your IP.")

    _clear_failed_logins(lock_key)
    sid = await create_session(user, ip, request.headers.get("user-agent", ""))
    await open_user_session_row(db, sid, user, ip, request)

    user.last_login_at = func.now()
    db.commit()
    
    await audit.record("login", actor=user, request=request)
    
    response.set_cookie("rca_session", sid, httponly=True, secure=settings.session_cookie_secure, samesite="strict", max_age=SESSION_TTL)
    return {"role": user.role, "must_change_password": user.must_change_password}

@router.post("/logout")
async def logout(request: Request, response: Response, user = Depends(get_current_user), db = Depends(get_db)):
    sid = getattr(request.state, "session_id", None)
    if sid:
        await revoke_session(sid)
        session_row = db.get(UserSession, sid)
        if session_row:
            session_row.logout_at = func.now()
            session_row.status = "closed"
            db.commit()
    await audit.record("logout", actor=user, request=request)
    response.delete_cookie("rca_session")
    return {"detail": "Logged out"}

@router.get("/me")
async def get_me(user = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password
    }

@router.post("/heartbeat")
async def heartbeat(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    sid = getattr(request.state, "session_id", None)
    if sid:
        session_row = db.get(UserSession, sid)
        if session_row:
            session_row.last_seen_at = func.now()
            db.commit()
    return {"detail": "ok"}

@router.post("/change-password")
async def change_password(body: PasswordChangeIn, request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash or ""):
        raise HTTPException(400, "Incorrect current password.")
    
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.password_updated_at = func.now()
    db.commit()
    
    await audit.record("password_changed", actor=user, request=request)
    await revoke_all_for_user(str(user.id))
    
    return {"detail": "Password updated, please log in again."}
