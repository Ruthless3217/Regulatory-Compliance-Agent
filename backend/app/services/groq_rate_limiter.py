"""Groq token rate limiting (Priority 4).

Groq enforces both tokens-per-minute (TPM) and tokens-per-day (TPD) ceilings
per model. This module tracks usage with a sliding 60s window (deque) for TPM
and a midnight-reset counter for TPD, instantiated separately per model.

When the daily counter crosses a configurable fraction (default 90%) of the
TPD ceiling, ``acquire`` raises :class:`DailyLimitApproaching` so the caller
can queue the submission (``status="pending_token_budget"``) until the daily
reset rather than burn the remaining budget on a partial run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class DailyLimitApproaching(Exception):
    """Raised when a model's daily token budget would be exceeded by a call."""

    def __init__(self, model: str, day_tokens: int, daily_cap: int):
        self.model = model
        self.day_tokens = day_tokens
        self.daily_cap = daily_cap
        super().__init__(
            f"{model}: daily token cap approaching ({day_tokens}/{daily_cap})"
        )


class GroqRateLimiter:
    """Per-model TPM (sliding window) + TPD (daily) token tracker."""

    def __init__(
        self,
        model: str,
        tpm_limit: int,
        tpd_limit: int,
        daily_cap_fraction: float = 0.9,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.model = model
        self.tpm_limit = tpm_limit
        self.tpd_limit = tpd_limit
        self.daily_cap_fraction = daily_cap_fraction
        self._clock = clock or time.time
        self._minute: Deque[Tuple[float, int]] = deque()  # (timestamp, tokens)
        self._minute_sum = 0
        self._day_tokens = 0
        self._day_index: Optional[int] = None

    # --- internal -------------------------------------------------------

    def _refresh(self) -> None:
        """Roll the day counter at UTC midnight and prune the minute window."""
        ts = self._clock()
        di = int(ts // 86_400)
        if self._day_index != di:
            self._day_index = di
            self._day_tokens = 0
        cutoff = ts - 60.0
        while self._minute and self._minute[0][0] <= cutoff:
            _, tok = self._minute.popleft()
            self._minute_sum -= tok

    # --- inspection -----------------------------------------------------

    @property
    def daily_cap(self) -> int:
        return int(self.tpd_limit * self.daily_cap_fraction)

    @property
    def day_tokens(self) -> int:
        self._refresh()
        return self._day_tokens

    @property
    def minute_tokens(self) -> int:
        self._refresh()
        return self._minute_sum

    def would_exceed_daily(self, tokens: int) -> bool:
        self._refresh()
        return self._day_tokens + tokens > self.daily_cap

    def seconds_until_minute_capacity(self, tokens: int) -> float:
        """How long to wait until ``tokens`` more fit inside the TPM window."""
        self._refresh()
        if self._minute_sum + tokens <= self.tpm_limit:
            return 0.0
        ts = self._clock()
        need = self._minute_sum + tokens - self.tpm_limit
        freed = 0
        for (t, tok) in self._minute:
            freed += tok
            if freed >= need:
                return max(0.0, (t + 60.0) - ts)
        return 60.0

    # --- mutation -------------------------------------------------------

    def record(self, tokens: int) -> None:
        """Record actual token spend against both windows."""
        if tokens <= 0:
            return
        self._refresh()
        self._minute.append((self._clock(), tokens))
        self._minute_sum += tokens
        self._day_tokens += tokens

    async def acquire(self, tokens: int) -> None:
        """Reserve ``tokens`` of budget. Raises DailyLimitApproaching when the
        daily cap would be crossed; otherwise sleeps until the TPM window has
        room, then records the (estimated) spend."""
        if self.would_exceed_daily(tokens):
            raise DailyLimitApproaching(self.model, self._day_tokens, self.daily_cap)
        wait = self.seconds_until_minute_capacity(tokens)
        if wait > 0:
            logger.info(f"{self.model}: TPM window full, sleeping {wait:.1f}s")
            await asyncio.sleep(wait)
            self._refresh()
        self.record(tokens)

    def reconcile(self, estimated: int, actual: int) -> None:
        """Correct the windows after a call, given the estimate already
        recorded by ``acquire`` and the actual token usage reported by Groq."""
        delta = actual - estimated
        if delta == 0:
            return
        self._refresh()
        self._day_tokens = max(0, self._day_tokens + delta)
        self._minute_sum = max(0, self._minute_sum + delta)
        if self._minute:
            ts, tok = self._minute[-1]
            self._minute[-1] = (ts, max(0, tok + delta))


# --- per-model registry -------------------------------------------------

_limiters: Dict[str, GroqRateLimiter] = {}


def get_rate_limiter(model: str, key_id: str = "default") -> GroqRateLimiter:
    """Return the process-wide limiter for ``(key_id, model)``, building it from
    settings on first use. Classify vs citation models carry different TPM/TPD
    ceilings.

    Groq enforces TPM/TPD *per API key*, so each key needs its own counter:
    failing over to a fresh key must not be blocked by another key's spend.
    ``key_id`` defaults to ``"default"`` so single-key callers are unaffected.
    """
    registry_key = f"{key_id}\x00{model}"
    if registry_key not in _limiters:
        from app.config import settings

        if model == settings.groq_classify_model:
            tpm, tpd = settings.groq_classify_tpm, settings.groq_classify_tpd
        else:
            tpm, tpd = settings.groq_citation_tpm, settings.groq_citation_tpd
        _limiters[registry_key] = GroqRateLimiter(
            model=model,
            tpm_limit=tpm,
            tpd_limit=tpd,
            daily_cap_fraction=settings.groq_daily_cap_fraction,
        )
    return _limiters[registry_key]


def reset_rate_limiters() -> None:
    """Test helper — drop all cached limiters."""
    _limiters.clear()
