"""A refused run is a verdict, not a missing result.

When `evaluate_persistability` fails closed there is no ComplianceCheck, so
`GET /compliance/results/{id}` used to fall through to "No compliance check
found. Run analysis first." — the same reply a never-analysed submission gets.
The reviewer of the 2026-08-30 production run therefore saw a document that
looked un-analysed while the run record held `degraded_reason` explaining the
deliberate refusal.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.api.routes import compliance as compliance_routes
from app.models.analysis_run import AnalysisRun
from app.models.compliance_check import ComplianceCheck
from app.models.review_assignment import ReviewAssignment
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.violation import Violation

SUB_ID = uuid.uuid4()
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)


class _Admin:
    """Unrestricted caller — visibility is not what these tests are about."""

    id = uuid.uuid4()
    role = "admin"


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_):
        return self

    def order_by(self, *_):
        return self

    def limit(self, *_):
        return self

    def scalar(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Db:
    def __init__(self, submission, check=None, runs=()):
        self._submission = submission
        self._check = check
        self._runs = list(runs)

    def query(self, target):
        if target is ReviewAssignment:
            return _Query([])
        if target is Submission:
            return _Query([self._submission] if self._submission else [])
        if target is ComplianceCheck:
            return _Query([self._check] if self._check else [])
        if target is AnalysisRun:
            return _Query(self._runs)
        if target is Violation:
            return _Query([])
        # export_common.findings_are_stale queries the revision timestamp
        # column; no post-analysis edits in these fixtures.
        if target is SubmissionRevision.created_at:
            return _Query([])
        raise AssertionError(f"unexpected query target {target!r}")


def _submission(status):
    return Submission(id=SUB_ID, title="Range comparison sheet", status=status)


def _run(status, degraded_reason, run_number=1):
    return AnalysisRun(
        id=uuid.uuid4(),
        submission_id=SUB_ID,
        run_number=run_number,
        status=status,
        degraded_reason=degraded_reason,
        started_at=NOW,
        finished_at=NOW,
    )


def _results(db):
    return asyncio.run(
        compliance_routes.get_compliance_results(str(SUB_ID), user=_Admin(), db=db)
    )


def test_needs_review_is_reported_as_a_refusal_not_a_missing_analysis():
    db = _Db(
        _submission("needs_review"),
        runs=[_run("needs_review", "product_unresolved")],
    )

    body = _results(db)

    assert body["status"] == "needs_review"
    assert body["degraded_reason"] == "product_unresolved"
    assert body["analysis_state"] == "needs_review"
    # The old reply told the reviewer to do the one thing that had already
    # happened. It must not survive.
    assert "Run analysis first" not in body["message"]
    assert "review" in body["message"].lower()


def test_failed_run_is_distinguished_from_never_analysed():
    db = _Db(_submission("failed"), runs=[_run("failed", "no_content")])

    body = _results(db)

    assert body["analysis_state"] == "failed"
    assert body["degraded_reason"] == "no_content"
    assert "Run analysis first" not in body["message"]


def test_never_analysed_submission_still_asks_for_a_run():
    db = _Db(_submission("uploaded"), runs=[])

    body = _results(db)

    assert body["analysis_state"] == "not_analyzed"
    assert body["degraded_reason"] is None
    assert "Run analysis" in body["message"]


def test_latest_run_wins_when_a_rerun_followed_a_refusal():
    db = _Db(
        _submission("needs_review"),
        runs=[
            _run("needs_review", "product_ambiguous", run_number=2),
            _run("failed", "no_content", run_number=1),
        ],
    )

    assert _results(db)["degraded_reason"] == "product_ambiguous"


def test_graded_submission_is_unchanged_by_the_refusal_reporting():
    check = ComplianceCheck(
        id=uuid.uuid4(),
        submission_id=SUB_ID,
        overall_score=72,
        grade="B",
        status="review_required",
        scores={"overall": 72},
        checked_at=NOW,
    )
    db = _Db(_submission("analyzed"), check=check, runs=[_run("completed", None)])

    body = _results(db)

    assert body["overall_score"] == 72
    assert body["grade"] == "B"
    assert "message" not in body
    assert body.get("degraded_reason") is None
