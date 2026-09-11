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


def _run(status, degraded_reason, run_number=1, run_metadata=None, check_id=None):
    return AnalysisRun(
        id=uuid.uuid4(),
        submission_id=SUB_ID,
        run_number=run_number,
        status=status,
        degraded_reason=degraded_reason,
        run_metadata=run_metadata,
        compliance_check_id=check_id,
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
    assert body["analysis_state"] == "completed"
    assert body["analysis_warnings"] == []


# --------------------------------------------------------------------------
# A graded run can still be a PARTIAL one. Between "refused" and "certified"
# there is now a third answer, and it has to be legible.
# --------------------------------------------------------------------------


def _graded_check(status="flagged"):
    return ComplianceCheck(
        id=uuid.uuid4(),
        submission_id=SUB_ID,
        overall_score=88,
        grade="B",
        status=status,
        scores={"overall": 88},
        checked_at=NOW,
    )


WARNED_RUN_METADATA = {
    "analysis_warnings": [
        {
            "code": "rider_uins_without_fact_cards",
            "detail": {"uins": ["116N216V01"], "chunk_indexes": [24]},
        },
        {"code": "precedent_evidence_unavailable", "detail": {"precedents": 0}},
    ],
    "grounded_evidence": {"rules": 0, "precedents": 0, "product_facts": 2},
}


def test_a_partially_grounded_grade_is_reported_as_such():
    check = _graded_check()
    db = _Db(
        _submission("analyzed"), check=check,
        runs=[_run("completed", None, run_metadata=WARNED_RUN_METADATA,
                   check_id=check.id)],
    )

    body = _results(db)

    assert body["analysis_state"] == "completed_with_warnings"
    assert body["overall_score"] == 88, "the score reports what was found"
    assert body["compliance_status"] == "flagged", "but it is not a clean pass"


def test_each_warning_names_what_the_grade_does_not_cover():
    check = _graded_check()
    db = _Db(
        _submission("analyzed"), check=check,
        runs=[_run("completed", None, run_metadata=WARNED_RUN_METADATA,
                   check_id=check.id)],
    )

    warnings = {w["code"]: w for w in _results(db)["analysis_warnings"]}

    assert set(warnings) == {
        "rider_uins_without_fact_cards", "precedent_evidence_unavailable",
    }
    rider = warnings["rider_uins_without_fact_cards"]
    assert rider["detail"]["uins"] == ["116N216V01"]
    assert rider["detail"]["chunk_indexes"] == [24]
    # An internal token is not an explanation. Every warning must arrive with
    # one, or the reviewer is back to guessing what the grade means.
    assert all(w["explanation"] for w in warnings.values())


def test_an_unrecognised_warning_code_is_still_surfaced():
    """A warning added by a newer backend must never vanish because this
    endpoint has no phrasing for it yet — silence would read as 'fully
    grounded'."""
    check = _graded_check()
    db = _Db(
        _submission("analyzed"), check=check,
        runs=[_run("completed", None, check_id=check.id,
                   run_metadata={"analysis_warnings": [{"code": "brand_new"}]})],
    )

    body = _results(db)

    assert body["analysis_state"] == "completed_with_warnings"
    assert [w["code"] for w in body["analysis_warnings"]] == ["brand_new"]
