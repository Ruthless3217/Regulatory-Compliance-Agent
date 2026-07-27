"""FastAPI auth dependencies (audit-trail 05 §2.4).

- ``get_current_user`` loads the Redis session, re-checks the bound IP (cookie-
  theft defense), loads the (active) user via the SYNC db session, and stashes
  session_id/user on ``request.state``.
- ``require(permission)`` is the RBAC gate factory used on every protected route.
- ``ip_allowed_for_user`` implements the four D1 binding modes.

The app uses synchronous SQLAlchemy, so ``db.get(...)`` is a sync call.
"""
from __future__ import annotations

import ipaddress
import uuid

from fastapi import Depends, HTTPException, Request

from app.config import settings
from app.database import get_db
from app.api.rate_limit import extract_client_key
from app.models.user import User
from .permissions import role_has
from .sessions import load_session


def ip_allowed_for_user(user, client_ip: str, mode: str | None = None) -> bool:
    """Whether ``client_ip`` is acceptable for ``user`` under the binding mode."""
    mode = mode or settings.auth_ip_binding_mode
    if mode == "log_only":
        return True
    if mode == "strict":
        return bool(user.registered_ip) and client_ip == user.registered_ip
    if mode == "cidr":
        if not getattr(user, "allowed_cidr", None):
            return False
        try:
            return ipaddress.ip_address(client_ip) in ipaddress.ip_network(user.allowed_cidr, strict=False)
        except ValueError:
            return False
    if mode == "list":
        return client_ip in (getattr(user, "allowed_ips", None) or [])
    return False


def _load_user(db, user_id: str):
    try:
        pk = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    return db.get(User, pk)


async def get_current_user(request: Request, db=Depends(get_db)) -> User:
    sid = request.cookies.get("rca_session")
    sess = await load_session(sid) if sid else None
    if not sess:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = _load_user(db, sess.get("user_id"))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Re-bind the session to the caller IP (defends a stolen cookie used from a
    # different machine). Skipped in log_only mode.
    if settings.auth_ip_binding_mode != "log_only":
        client_ip = extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        if not ip_allowed_for_user(user, client_ip):
            raise HTTPException(status_code=401, detail="Session ended — please log in again.")

    request.state.session_id = sid
    request.state.current_user = user
    return user


def require(permission: str):
    """Dependency factory: 403 (and an audit event) unless the caller's role is
    granted ``permission``. Server-side and authoritative — UI hiding is not the
    gate."""

    async def _dep(request: Request, user: User = Depends(get_current_user)) -> User:
        if not role_has(user.role, permission):
            try:  # best-effort audit (module may be wired in a later phase)
                from app.services.observability import audit

                await audit.record(
                    event_type="authz_denied", actor=user, request=request,
                    metadata={"permission": permission, "path": str(request.url.path)},
                )
            except Exception:
                pass
            raise HTTPException(status_code=403, detail="You don't have access to this.")
        return user

    return _dep
