"""Phase 4 (observability): analysis_runs open/close (re-run + cost rollup).

Uses a hand-rolled *stub* sync SQLAlchemy session (no Postgres — the ORM models
use UUID/JSONB/Numeric which sqlite can't honour). The stub answers the two
query shapes run_tracker needs:

  * open():  db.query(AnalysisRun).filter_by(submission_id=...).count()
  * close(): db.query(<sum exprs>).filter(...).one()  -> canned (in, out, tot, cost)

The CRITICAL assertion is that a fail-closed run (compliance_check_id=None,
status needs_review/failed) STILL records the tokens/cost summed from its usage
events — that is exactly the money the console must not lose.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.services import run_tracker


class _FakeQuery:
    def __init__(self, count_value, sum_row):
        self._count = count_value
        self._sum_row = sum_row

    def filter_by(self, **kw):        # used by open()
        return self

    def filter(self, *a, **kw):       # used by close()
        return self

    def count(self):
        return self._count

    def one(self):
        return self._sum_row


class _FakeSession:
    """Minimal stand-in for a sync SQLAlchemy Session."""

    def __init__(self, existing_runs=0, sum_row=(0, 0, 0, Decimal("0"))):
        self._existing_runs = existing_runs
        self._sum_row = sum_row
        self.added = []
        self.commits = 0

    def query(self, *entities):
        # open() passes the AnalysisRun class; close() passes SUM expressions.
        return _FakeQuery(self._existing_runs, self._sum_row)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def _user(uid="user-7"):
    return SimpleNamespace(id=uid)


def test_open_first_run_is_not_a_rerun():
    db = _FakeSession(existing_runs=0)
    run = run_tracker.open(
        db, submission_id="sub-1", user=_user(), session_id="sess-1", trigger_source="sync"
    )
    assert run.run_number == 1
    assert run.is_rerun is False
    assert run.status == "running"
    assert run.submission_id == "sub-1"
    assert run.triggered_by == "user-7"
    assert run.session_id == "sess-1"
    assert run.trigger_source == "sync"
    assert run in db.added
    assert db.commits == 1


def test_open_second_run_flips_is_rerun():
    db = _FakeSession(existing_runs=1)   # one prior run exists
    run = run_tracker.open(
        db, submission_id="sub-1", user=_user(), session_id="sess-2", trigger_source="async"
    )
    assert run.run_number == 2
    assert run.is_rerun is True


def test_close_rolls_up_tokens_and_cost():
    db = _FakeSession(existing_runs=0, sum_row=(1200, 800, 2000, Decimal("0.1234")))
    run = run_tracker.open(
        db, submission_id="sub-2", user=_user(), session_id="s", trigger_source="sync"
    )
    # give it a start so duration is computable
    run.started_at = datetime.now(timezone.utc) - timedelta(seconds=3)

    closed = run_tracker.close(
        db, run, status="completed", compliance_check_id="check-99"
    )
    assert closed.status == "completed"
    assert closed.compliance_check_id == "check-99"
    assert closed.prompt_tokens == 1200
    assert closed.completion_tokens == 800
    assert closed.total_tokens == 2000
    assert closed.total_cost_usd == Decimal("0.1234")
    assert closed.finished_at is not None
    assert closed.duration_ms is not None and closed.duration_ms >= 0


def test_fail_closed_run_still_records_tokens_and_cost():
    """CRITICAL: a degraded/failed run persists NO ComplianceCheck
    (compliance_check_id is None) yet already spent tokens — those must be
    rolled up onto the analysis_runs row anyway."""
    db = _FakeSession(existing_runs=1, sum_row=(500, 250, 750, Decimal("0.0500")))
    run = run_tracker.open(
        db, submission_id="sub-3", user=_user(), session_id="s", trigger_source="stream"
    )
    assert run.is_rerun is True  # 2nd attempt

    closed = run_tracker.close(
        db,
        run,
        status="needs_review",
        degraded_reason="critic_timeout",
        compliance_check_id=None,          # fail-closed: no grade persisted
    )
    assert closed.compliance_check_id is None
    assert closed.status == "needs_review"
    assert closed.degraded_reason == "critic_timeout"
    # the money is still captured
    assert closed.prompt_tokens == 500
    assert closed.completion_tokens == 250
    assert closed.total_tokens == 750
    assert closed.total_cost_usd == Decimal("0.0500")


def test_close_handles_no_usage_events():
    db = _FakeSession(existing_runs=0, sum_row=(0, 0, 0, Decimal("0")))
    run = run_tracker.open(
        db, submission_id="sub-4", user=_user(), session_id="s", trigger_source="sync"
    )
    closed = run_tracker.close(db, run, status="failed", compliance_check_id=None)
    assert closed.prompt_tokens == 0
    assert closed.total_cost_usd == Decimal("0")
    assert closed.status == "failed"
