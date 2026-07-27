"""Request-scoped attribution context for LLM usage metering.

Mirrors the existing ``GraphContext`` ContextVar pattern
(``app/services/agents/graph/context.py``): a request-scoped ``ContextVar`` that
carries the ``user -> session -> submission -> run`` attribution chain down to
``LLMService`` (which is far below the HTTP/graph layers and otherwise has no
idea who triggered a call).

Because ``ContextVar``s are copied into ``asyncio`` tasks, the bounded-concurrency
``asyncio.gather`` over document chunks still attributes each child LLM call to
the correct run.

Who sets what (see spec 02 §3):
- auth middleware      -> user_id, session_id
- route handlers       -> feature, submission_id (where known)
- ComplianceEngine     -> run_id, submission_id, feature="analysis"
Who reads it: ``usage_recorder.record()``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class UsageContext:
    """Immutable attribution snapshot for the current logical unit of work."""

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    submission_id: Optional[str] = None
    run_id: Optional[str] = None            # analysis_runs.id
    feature: str = "unknown"                # analysis|chat|quote|rewrite|rule_generation|ingestion


# Module-level ContextVar with a safe empty default so ``get_usage_context()``
# never raises even outside a request (e.g. ingestion scripts, tests).
_ctx: ContextVar[UsageContext] = ContextVar("usage_context", default=UsageContext())


def get_usage_context() -> UsageContext:
    """Return the current attribution context (never ``None``)."""
    return _ctx.get()


def set_usage_context(**kw) -> Token:
    """Merge ``**kw`` onto the current context and install the new snapshot.

    Uses ``dataclasses.replace`` so callers can update just the fields they know
    (e.g. the engine adds ``run_id`` on top of the middleware's ``user_id``).
    Returns the ``Token`` for optional ``reset``.
    """
    return _ctx.set(replace(_ctx.get(), **kw))


def bind_usage_context(ctx: UsageContext) -> Token:
    """Replace the whole context with ``ctx``. Returns the ``Token`` for reset."""
    return _ctx.set(ctx)


def reset_usage_context(token: Token) -> None:
    """Restore the context to the value captured by ``token`` (finally-reset
    discipline, matching ``GraphContext.reset``). Safe if the token was created
    in a different context."""
    try:
        _ctx.reset(token)
    except (ValueError, LookupError):
        pass
