"""Redis-backed server-side sessions (audit-trail 01 §1.3).

The browser holds only an opaque 256-bit ``sid`` in an httpOnly cookie; all
session state lives in Redis under ``session:{sid}``. Fail-closed: if Redis is
unavailable, ``create_session`` raises (login → 503) and ``load_session``
returns None (request → 401). Auth must never silently degrade to "no session".

Sliding absolute expiry + an idle timeout are both enforced. The durable
``user_sessions`` row (for session-time reporting) is written separately by the
auth routes / middleware; this module owns only the hot Redis session.
"""
from __future__ import annotations

import json
import secrets
import time

from app.config import settings
from app.services.cache.redis_client import get_redis


def _key(sid: str) -> str:
    return f"session:{sid}"


async def create_session(user, ip: str, user_agent: str) -> str:
    """Create a session for ``user`` bound to ``ip``/``user_agent``; return the sid.

    Raises RuntimeError when Redis is unavailable (auth fails closed)."""
    r = await get_redis()
    if r is None:
        raise RuntimeError("session store unavailable")
    sid = secrets.token_urlsafe(32)
    now = time.time()
    payload = {
        "user_id": str(user.id),
        "role": user.role,
        "ip": ip,
        "user_agent": user_agent,
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "created_at": now,
        "last_seen": now,
    }
    await r.set(_key(sid), json.dumps(payload), ex=settings.session_absolute_ttl_seconds)
    return sid


async def load_session(sid: str) -> dict | None:
    """Load + refresh a session. Returns None if missing, idle-expired, or Redis
    is down."""
    r = await get_redis()
    if r is None:
        return None
    raw = await r.get(_key(sid))
    if not raw:
        return None
    payload = json.loads(raw)
    # Idle timeout.
    if time.time() - float(payload.get("last_seen", 0)) > settings.session_idle_ttl_seconds:
        await r.delete(_key(sid))
        return None
    # Sliding refresh: bump last_seen and reset the absolute TTL window.
    payload["last_seen"] = time.time()
    await r.set(_key(sid), json.dumps(payload), ex=settings.session_absolute_ttl_seconds)
    return payload


async def revoke_session(sid: str) -> None:
    r = await get_redis()
    if r:
        await r.delete(_key(sid))


async def revoke_all_for_user(user_id: str) -> None:
    """Delete every session belonging to ``user_id`` (disable / force-logout)."""
    r = await get_redis()
    if not r:
        return
    async for k in r.scan_iter(match="session:*"):
        raw = await r.get(k)
        if raw and json.loads(raw).get("user_id") == user_id:
            await r.delete(k)
