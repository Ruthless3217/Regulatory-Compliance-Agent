"""Best-effort LLM usage recorder.

Called from ``LLMService`` at the exact point it already reads ``response.usage``
and feeds LangSmith. For every *billed* LLM call it writes one
``llm_usage_events`` row, stamped with the current ``usage_context`` attribution
and the computed USD cost.

Reliability contract (spec 02 §9): this is **best-effort** — losing a metering
row must NEVER break the compliance grade. All DB work is wrapped in
try/except, logs a warning, and never raises. The synchronous DB write runs in a
worker thread via ``asyncio.to_thread`` so the event loop is never blocked (the
app uses **synchronous** SQLAlchemy).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .cost import compute_cost, price_source_for
from .usage_context import get_usage_context

logger = logging.getLogger(__name__)


async def record(
    *,
    model: str,
    provider: Optional[str],
    profile: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: Optional[int] = None,
    is_retry: bool = False,
    token_source: str = "measured",
) -> None:
    """Record one billed LLM call. Never raises.

    Reads the current ``usage_context`` for attribution, computes cost from the
    model's price table, and inserts an ``LlmUsageEvent`` via a short-lived
    session in a worker thread.
    """
    try:
        ctx = get_usage_context()
        input_cost, output_cost, total_cost = compute_cost(
            model, prompt_tokens, completion_tokens
        )
        price_source = price_source_for(model)
        await asyncio.to_thread(
            _insert_usage_event,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            submission_id=ctx.submission_id,
            run_id=ctx.run_id,
            feature=ctx.feature,
            profile=profile,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(prompt_tokens or 0) + (completion_tokens or 0),
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            total_cost_usd=total_cost,
            token_source=token_source,
            price_source=price_source,
            latency_ms=latency_ms,
            is_retry=is_retry,
        )
    except Exception as e:  # noqa: BLE001 - metering must not break a grade
        logger.warning("usage_recorder: dropped a usage event: %s", e)


def _insert_usage_event(**fields) -> None:
    """Synchronous insert of one ``LlmUsageEvent`` in its own short-lived session.

    Imports are local so the module stays import-safe and the DB engine is only
    touched when an event is actually written.
    """
    from app.database import SessionLocal
    from app.models.llm_usage_event import LlmUsageEvent

    db = SessionLocal()
    try:
        db.add(LlmUsageEvent(**fields))
        db.commit()
    finally:
        db.close()
