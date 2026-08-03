"""find_stale_running_run: the orphan-reclaim guard for a submission wedged
on 'analyzing' because the process that owned its AnalysisRun was killed
mid-flight (container restart, OOM, --reload) before it could self-close.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.services.agents.compliance.scoring import ScoringService
from app.services.run_tracker import find_stale_running_run, open_run


def _run(status="running", finished_at=None, started_at=None):
    run = MagicMock()
    run.status = status
    run.finished_at = finished_at
    run.started_at = started_at
    return run


def _db_returning(run):
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = run
    return db


def test_open_run_stamps_the_active_scoring_policy(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 0
    monkeypatch.setattr("app.services.run_tracker.audit.record", AsyncMock())

    run = asyncio.run(open_run(db, "00000000-0000-0000-0000-000000000001", None, "test"))
    active_policy = ScoringService.calculate_scores([])["scoring_policy_version"]

    assert active_policy == "absolute-soft-tail-v2"
    assert run.scoring_policy_version == active_policy


def test_no_run_is_not_stale():
    db = _db_returning(None)
    assert asyncio.run(find_stale_running_run(db, "sub-1", 15)) is None


def test_finished_run_is_not_stale():
    run = _run(status="completed", finished_at=datetime.now(timezone.utc))
    db = _db_returning(run)
    assert asyncio.run(find_stale_running_run(db, "sub-1", 15)) is None


def test_recent_running_run_is_not_stale():
    run = _run(started_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db = _db_returning(run)
    assert asyncio.run(find_stale_running_run(db, "sub-1", 15)) is None


def test_old_running_run_is_stale():
    run = _run(started_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    db = _db_returning(run)
    assert asyncio.run(find_stale_running_run(db, "sub-1", 15)) is run


def test_naive_started_at_is_handled():
    # TIMESTAMPTZ columns should come back tz-aware, but guard against a
    # naive datetime slipping through (e.g. a differently-configured DB).
    run = _run(started_at=datetime.utcnow() - timedelta(minutes=30))
    db = _db_returning(run)
    assert asyncio.run(find_stale_running_run(db, "sub-1", 15)) is run
