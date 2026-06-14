"""HTTP rate limiting for LLM-facing endpoints.

Redis-backed fixed-window counter (shared across uvicorn workers) keyed on the
caller PRINCIPAL — the ``X-API-Key`` header when present, else client IP. Falls
back to an in-process counter when Redis is unavailable, so the guard degrades
rather than disappearing. See architect-audit H8 (the old limiter was
per-process and IP-only, so N workers = N× the limit and a proxy collapsed every
caller into one bucket).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional, Tuple

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


class FixedWindowLimiter:
    """Per-key fixed-window request counter (in-process fallback)."""

    def __init__(self, limit: int, window_seconds: float, clock: Optional[Callable[[], float]] = None):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock or time.time
        self._buckets: Dict[str, Tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        """Record a request for `key`; return False if it exceeds the limit
        within the current window."""
        now = self._clock()
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        count += 1
        self._buckets[key] = (start, count)
        return count <= self.limit


# In-process fallback limiter for the paid LLM endpoints.
_llm_limiter = FixedWindowLimiter(settings.http_rate_limit_per_min, 60.0)
_WINDOW_SECONDS = 60


def _principal(request: Request) -> str:
    """Identify the caller for rate-limiting. Prefer an API key header (stable
    per-client) over IP, which behind a proxy is the proxy's address."""
    api_key = request.headers.get("x-api-key") or request.headers.get("authorization")
    if api_key:
        # Don't key on the raw secret — use a short stable suffix.
        return f"key:{api_key.strip()[-12:]}"
    return f"ip:{request.client.host}" if request.client else "ip:unknown"


async def _redis_allow(principal: str) -> Optional[bool]:
    """Redis-backed window. Returns True/False, or None if Redis is unavailable
    (so the caller falls back to the in-process limiter)."""
    try:
        from app.services.cache.redis_client import get_redis
        r = await get_redis()
        if r is None:
            return None
        bucket = int(time.time() // _WINDOW_SECONDS)
        key = f"httprl:llm:{principal}:{bucket}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, _WINDOW_SECONDS * 2)
        return int(count) <= settings.http_rate_limit_per_min
    except Exception as e:  # pragma: no cover
        logger.debug(f"HTTP rate limit: Redis unavailable, using in-process: {e}")
        return None


async def llm_rate_limit(request: Request) -> None:
    """FastAPI dependency: 429 when the caller exceeds the per-minute budget
    for LLM-facing endpoints."""
    principal = _principal(request)
    allowed = await _redis_allow(principal)
    if allowed is None:  # Redis down → in-process fallback
        allowed = _llm_limiter.allow(principal)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for analysis endpoints; please slow down.",
        )
