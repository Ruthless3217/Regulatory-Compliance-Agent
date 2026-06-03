"""Lightweight in-process HTTP rate limiting for LLM-facing endpoints.

No external dependency (slowapi not available). A fixed-window counter keyed on
caller IP bounds how often the expensive, paid LLM endpoints can be hit — a
stopgap until auth + an API-gateway limiter land. See architect-audit H8.

NOTE: this is per-process. Behind multiple workers each has its own window;
for a hard global limit use a shared store (Redis) — tracked separately.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Tuple

from fastapi import HTTPException, Request

from app.config import settings


class FixedWindowLimiter:
    """Per-key fixed-window request counter."""

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


# Process-wide limiter for the paid LLM endpoints.
_llm_limiter = FixedWindowLimiter(settings.http_rate_limit_per_min, 60.0)


async def llm_rate_limit(request: Request) -> None:
    """FastAPI dependency: 429 when the caller exceeds the per-minute budget
    for LLM-facing endpoints."""
    key = request.client.host if request.client else "unknown"
    if not _llm_limiter.allow(key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for analysis endpoints; please slow down.",
        )
