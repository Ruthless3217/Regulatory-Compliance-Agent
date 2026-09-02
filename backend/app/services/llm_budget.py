"""Global LLM token budget — a hard daily ceiling across ALL keys, models, and
endpoints (analysis + chat + plain), independent of any provider-side TPM/TPD
ceiling.

A provider's own per-key quota is enforced reactively: a 429 rotates to the
next key (see ``_is_rate_limit_error``). This is the proactive guard, and it
protects the wallet / contract as a whole rather than one key. It is
Redis-backed so the ceiling is shared across uvicorn workers, with an
in-process fallback when Redis is down. Disabled by default
(``llm_global_daily_token_budget = 0``) so existing behaviour is unchanged until
an operator sets a budget.

Reserve BEFORE a call (estimate), reconcile AFTER (actual) so the counter tracks
real spend. When the daily budget is exhausted, ``reserve`` raises
:class:`LLMBudgetExceeded` and the caller fails closed.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class LLMBudgetExceeded(RuntimeError):
    """Raised when the global daily LLM token budget would be exceeded."""

    def __init__(self, spent: int, budget: int):
        self.spent = spent
        self.budget = budget
        super().__init__(f"Global LLM daily token budget exhausted ({spent}/{budget})")


def _day_index(clock: Callable[[], float]) -> int:
    return int(clock() // 86_400)


class GlobalTokenBudget:
    """Daily token ceiling shared across all callers.

    Uses Redis (atomic INCRBY on a day-keyed counter that expires after 2 days)
    when available; otherwise an in-process counter that resets at UTC midnight.
    """

    def __init__(self, budget: int, clock: Optional[Callable[[], float]] = None):
        self.budget = budget
        self._clock = clock or time.time
        self._proc_day: Optional[int] = None
        self._proc_tokens = 0

    @property
    def enabled(self) -> bool:
        return bool(self.budget and self.budget > 0)

    def _key(self) -> str:
        return f"llm:global:tokens:{_day_index(self._clock)}"

    # --- in-process fallback ------------------------------------------------

    def _proc_refresh(self) -> None:
        di = _day_index(self._clock)
        if self._proc_day != di:
            self._proc_day = di
            self._proc_tokens = 0

    def _proc_spent(self) -> int:
        self._proc_refresh()
        return self._proc_tokens

    def _proc_add(self, tokens: int) -> int:
        self._proc_refresh()
        self._proc_tokens = max(0, self._proc_tokens + tokens)
        return self._proc_tokens

    # --- public async surface ----------------------------------------------

    async def _redis(self):
        try:
            from app.services.cache.redis_client import get_redis
            return await get_redis()
        except Exception:  # pragma: no cover
            return None

    async def spent_today(self) -> int:
        if not self.enabled:
            return 0
        r = await self._redis()
        if r is None:
            return self._proc_spent()
        try:
            val = await r.get(self._key())
            return int(val) if val is not None else 0
        except Exception as e:  # pragma: no cover
            logger.debug(f"Global budget: Redis read failed, using in-process: {e}")
            return self._proc_spent()

    async def reserve(self, tokens: int) -> None:
        """Reserve ``tokens`` against the daily budget; raise LLMBudgetExceeded
        if that would cross the ceiling. No-op when disabled."""
        if not self.enabled or tokens <= 0:
            return
        r = await self._redis()
        if r is None:
            spent = self._proc_add(tokens)
            if spent > self.budget:
                self._proc_add(-tokens)  # roll back the reservation
                raise LLMBudgetExceeded(spent - tokens, self.budget)
            return
        try:
            key = self._key()
            spent = await r.incrby(key, tokens)
            # Expire ~2 days out so the counter self-cleans after the day rolls.
            await r.expire(key, 172_800)
            if spent > self.budget:
                await r.incrby(key, -tokens)  # roll back
                raise LLMBudgetExceeded(int(spent) - tokens, self.budget)
        except LLMBudgetExceeded:
            raise
        except Exception as e:  # pragma: no cover
            logger.debug(f"Global budget: Redis reserve failed, using in-process: {e}")
            spent = self._proc_add(tokens)
            if spent > self.budget:
                self._proc_add(-tokens)
                raise LLMBudgetExceeded(spent - tokens, self.budget)

    async def reconcile(self, estimated: int, actual: int) -> None:
        """Adjust the counter by (actual − estimated) after a call. No-op when
        disabled or when the delta is zero."""
        if not self.enabled:
            return
        delta = (actual or 0) - (estimated or 0)
        if delta == 0:
            return
        r = await self._redis()
        if r is None:
            self._proc_add(delta)
            return
        try:
            key = self._key()
            await r.incrby(key, delta)
            await r.expire(key, 172_800)
        except Exception as e:  # pragma: no cover
            logger.debug(f"Global budget: Redis reconcile failed: {e}")
            self._proc_add(delta)


_budget: Optional[GlobalTokenBudget] = None


def get_global_budget() -> GlobalTokenBudget:
    """Process-wide singleton, built from settings on first use."""
    global _budget
    if _budget is None:
        from app.config import settings
        _budget = GlobalTokenBudget(settings.llm_global_daily_token_budget)
    return _budget


async def llm_budget_guard() -> None:
    """FastAPI dependency — reject new LLM-backed requests once the global daily
    token budget is already exhausted, so a single endpoint can't keep spending
    past the ceiling. No-op when the budget is disabled
    (``llm_global_daily_token_budget = 0``, the default).

    This is a coarse pre-request gate; precise per-call accounting still happens
    via ``GlobalTokenBudget.reserve``/``reconcile`` inside the LLM service.
    """
    budget = get_global_budget()
    if not budget.enabled:
        return
    spent = await budget.spent_today()
    if spent >= budget.budget:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Global daily LLM token budget exhausted ({spent}/{budget.budget}). "
                "Requests resume after UTC midnight."
            ),
        )
