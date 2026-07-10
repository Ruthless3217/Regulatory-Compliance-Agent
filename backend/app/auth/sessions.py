import secrets
import json
from ..services.cache.redis_client import get_redis
from ..config import settings

SESSION_TTL = settings.session_absolute_ttl_seconds
IDLE_TTL = settings.session_idle_ttl_seconds

async def create_session(user, ip, user_agent) -> str:
    sid = secrets.token_urlsafe(32)
    r = await get_redis()
    if r is None:
        raise RuntimeError("session store unavailable")
    
    payload = {
        "user_id": str(user.id),
        "role": user.role,
        "ip": ip,
        "user_agent": user_agent,
        "must_change_password": user.must_change_password
    }
    await r.set(f"session:{sid}", json.dumps(payload), ex=SESSION_TTL)
    return sid

async def load_session(sid: str) -> dict | None:
    r = await get_redis()
    if r is None:
        return None
    raw = await r.get(f"session:{sid}")
    if not raw:
        return None
    await r.expire(f"session:{sid}", SESSION_TTL)
    return json.loads(raw)

async def revoke_session(sid: str):
    r = await get_redis()
    if r:
        await r.delete(f"session:{sid}")

async def revoke_all_for_user(user_id: str):
    r = await get_redis()
    if not r:
        return
    async for k in r.scan_iter(match="session:*"):
        raw = await r.get(k)
        if raw and json.loads(raw).get("user_id") == user_id:
            await r.delete(k)
