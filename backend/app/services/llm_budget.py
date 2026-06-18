"""Global daily LLM token budget — the hard backstop against runaway spend.

A shared Redis counter (`llm:tokens:<UTC-date>`) accumulates total tokens
(prompt+completion) across ALL callers. Endpoints call :func:`assert_within_budget`
before doing expensive work and fail closed (429) once the ceiling is reached;
the LLM service calls :func:`record_tokens` with the real usage after each call.

Disabled when ``settings.llm_daily_token_budget <= 0``. When Redis is
unavailable the ceiling cannot be enforced globally, so calls are ALLOWED (the
per-IP rate limiter still applies) and a warning is logged — we never block a
compliance analysis because of a cache outage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

# Two days so the counter for "yesterday" lingers briefly around the UTC
# rollover instead of vanishing mid-request; the date in the key is what scopes
# the budget to a single day.
_KEY_TTL_SECONDS = 48 * 3600


def _today_key() -> str:
    return f"llm:tokens:{datetime.now(timezone.utc).date().isoformat()}"


def is_over_budget(spent: int, budget: int) -> bool:
    """Pure decision: has cumulative ``spent`` reached the ``budget`` ceiling?

    ``budget <= 0`` disables the guard (never over). Reaching the ceiling fails
    closed — the call that would tip us to/over the limit is refused.
    """
    if budget <= 0:
        return False
    return spent >= budget


async def assert_within_budget() -> None:
    """Raise HTTP 429 when today's global token spend has hit the ceiling.

    No-op when the budget is disabled or Redis is unavailable.
    """
    budget = settings.llm_daily_token_budget
    if budget <= 0:
        return
    try:
        from app.services.cache.redis_client import get_redis

        redis = await get_redis()
        if redis is None:
            logger.warning("daily token budget set but Redis unavailable; cannot enforce")
            return
        raw = await redis.get(_today_key())
        spent = int(raw) if raw else 0
    except Exception as e:  # pragma: no cover - infra failure path
        logger.warning(f"token-budget check failed ({e}); allowing request")
        return

    if is_over_budget(spent, budget):
        logger.error(f"daily LLM token budget exhausted: {spent}/{budget}")
        raise HTTPException(
            status_code=429,
            detail="Daily AI usage budget reached; analysis is paused until tomorrow.",
        )


async def record_tokens(tokens: int) -> None:
    """Add ``tokens`` to today's global counter. Best-effort; never raises."""
    if not tokens or settings.llm_daily_token_budget <= 0:
        return
    try:
        from app.services.cache.redis_client import get_redis

        redis = await get_redis()
        if redis is None:
            return
        key = _today_key()
        total = await redis.incrby(key, int(tokens))
        if total == int(tokens):
            await redis.expire(key, _KEY_TTL_SECONDS)
    except Exception as e:  # pragma: no cover - infra failure path
        logger.debug(f"token-budget record failed: {e}")


async def llm_budget_guard() -> None:
    """FastAPI dependency wrapper around :func:`assert_within_budget`."""
    await assert_within_budget()
