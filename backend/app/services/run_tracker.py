"""Open/close ``analysis_runs`` rows around each ``analyze_submission``.

Why a first-class fact table (not derived from ``compliance_checks``)? The
engine's fail-closed persistability gate persists **no** ``ComplianceCheck`` for
degraded/failed runs — yet those runs already burned tokens. Counting runs by
``compliance_checks`` would under-count money on exactly the runs most worth
watching. So each invocation is its own ``analysis_runs`` row with its own actor,
timing, tokens, and cost. Every re-run is a distinct row.

Synchronous SQLAlchemy (``db.query``/``db.add``/``db.commit`` — no ``await``).

The ``close`` rollup ``SUM``s the run's ``llm_usage_events`` once at close, so the
row's totals are consistent and cheap to re-read. Crucially the ``SUM`` runs
regardless of status/persistability, so a fail-closed run is still fully costed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func

from app.models.analysis_run import AnalysisRun
from app.models.llm_usage_event import LlmUsageEvent


def open(
    db,
    *,
    submission_id: Any,
    user: Any,
    session_id: Optional[str],
    trigger_source: str,
) -> AnalysisRun:
    """Open a new ``analysis_runs`` row for this invocation.

    ``run_number = count(runs for submission) + 1``; ``is_rerun`` when > 1.
    Inserts with ``status="running"`` and returns the row.
    """
    run_number = (
        db.query(AnalysisRun).filter_by(submission_id=submission_id).count() + 1
    )
    run = AnalysisRun(
        submission_id=submission_id,
        triggered_by=(getattr(user, "id", None) if user is not None else None),
        session_id=session_id,
        run_number=run_number,
        is_rerun=run_number > 1,
        trigger_source=trigger_source,
        status="running",
    )
    db.add(run)
    db.commit()
    return run


def close(
    db,
    run: AnalysisRun,
    *,
    status: str,
    degraded_reason: Optional[str] = None,
    compliance_check_id: Any = None,
) -> AnalysisRun:
    """Finalise ``run``: roll up tokens/cost, set timing/status, commit.

    Sums ``prompt_tokens``/``completion_tokens``/``total_tokens``/``total_cost_usd``
    over ``llm_usage_events WHERE run_id = run.id`` and stamps them onto the row —
    for EVERY run, including fail-closed ones where ``compliance_check_id`` is
    ``None`` (no ``ComplianceCheck`` was persisted) but tokens were still spent.

    The SUM is a single aggregate query, structured so a stubbed session can
    answer it via ``.filter(...).one()`` returning ``(in, out, total, cost)``.
    """
    prompt_tokens, completion_tokens, total_tokens, total_cost = (
        db.query(
            func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.total_cost_usd), 0),
        )
        .filter(LlmUsageEvent.run_id == run.id)
        .one()
    )

    run.prompt_tokens = int(prompt_tokens or 0)
    run.completion_tokens = int(completion_tokens or 0)
    run.total_tokens = int(total_tokens or 0)
    run.total_cost_usd = total_cost if total_cost is not None else 0

    run.status = status
    run.degraded_reason = degraded_reason
    run.compliance_check_id = compliance_check_id

    run.finished_at = datetime.now(timezone.utc)
    started_at = getattr(run, "started_at", None)
    if started_at is not None:
        run.duration_ms = int((run.finished_at - started_at).total_seconds() * 1000)

    db.commit()
    return run
