"""Query-logic coverage for backend/app/api/routes/model_learning.py.

Every endpoint is called directly (bypassing FastAPI's DI — Depends()
defaults are irrelevant once you pass `user`/`db` as real kwargs) against a
real Postgres so the group_by/having/distinct-count SQL is actually
exercised, not a hand-rolled fake. Requires a reachable Postgres at
MODEL_LEARNING_TEST_DATABASE_URL (falls back to the local dev default);
skips the whole module if it can't connect — this suite verifies query
correctness, not environment plumbing.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "MODEL_LEARNING_TEST_DATABASE_URL",
    "postgresql://compliance_user:compliance_pass@localhost:55432/compliance_db",
)

try:
    _engine = create_engine(DATABASE_URL)
    _conn = _engine.connect()
    _conn.close()
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Postgres not reachable at {DATABASE_URL}: {exc}", allow_module_level=True)

from app.models.submission import Submission
from app.models.compliance_check import ComplianceCheck
from app.models.violation import Violation
from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.analysis_run import AnalysisRun
from app.models.rule_reliability_event import RuleReliabilityEvent
from app.api.routes import model_learning as ml

SessionLocal = sessionmaker(bind=_engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        # Truncate everything this suite could have written, in FK-safe order.
        session.rollback()
        session.query(RuleReliabilityEvent).delete()
        session.query(RuleFeedback).delete()
        session.query(Violation).delete()
        session.query(AnalysisRun).delete()
        session.query(ComplianceCheck).delete()
        session.query(Submission).delete()
        session.query(Rule).delete()
        session.commit()
        session.close()


FAKE_USER = {"id": None}


def _submission(db, **overrides) -> Submission:
    s = Submission(id=uuid.uuid4(), title="t", status="analyzed", original_content="x", content_type="text")
    for k, v in overrides.items():
        setattr(s, k, v)
    db.add(s)
    db.commit()
    return s


def _check(db, submission, **overrides) -> ComplianceCheck:
    c = ComplianceCheck(id=uuid.uuid4(), submission_id=submission.id)
    for k, v in overrides.items():
        setattr(c, k, v)
    db.add(c)
    db.commit()
    return c


def _rule(db, **overrides) -> Rule:
    r = Rule(id=uuid.uuid4(), category="regulatory", rule_text="do not do X", severity="high")
    for k, v in overrides.items():
        setattr(r, k, v)
    db.add(r)
    db.commit()
    return r


def _violation(db, check, **overrides) -> Violation:
    v = Violation(
        id=uuid.uuid4(),
        compliance_check_id=check.id,
        category="disclosure",
        severity="high",
        description="d",
    )
    for k, val in overrides.items():
        setattr(v, k, val)
    db.add(v)
    db.commit()
    return v


def _feedback(db, violation, **overrides) -> RuleFeedback:
    fb = RuleFeedback(id=uuid.uuid4(), violation_id=violation.id, verdict="correct", rule_id=violation.rule_id)
    for k, v in overrides.items():
        setattr(fb, k, v)
    db.add(fb)
    db.commit()
    return fb


def _run(db, submission, **overrides) -> AnalysisRun:
    r = AnalysisRun(
        id=uuid.uuid4(), submission_id=submission.id, run_number=1, status="running",
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    db.add(r)
    db.commit()
    return r


# --------------------------------------------------------------------------
# Empty-DB: no endpoint may 500 with zero rows anywhere.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_funnel_empty_db(db):
    out = await ml.get_learning_funnel(user=FAKE_USER, db=db)
    assert out == {
        "flagged": 0, "awaiting_review": 0, "feedback_collected": 0,
        "applied_to_scoring": 0, "reviewer_authored": 0,
        "no_gate_warning": out["no_gate_warning"], "status": "computed",
    }


@pytest.mark.asyncio
async def test_precision_empty_db_each_grouping(db):
    for by in ("rule", "category", "severity"):
        out = await ml.get_precision(by=by, user=FAKE_USER, db=db)
        assert out["groups"] == []
        assert out["status"] == "insufficient_data"


@pytest.mark.asyncio
async def test_calibration_empty_db(db):
    out = await ml.get_calibration(user=FAKE_USER, db=db)
    assert out["sample_size"] == 0
    assert out["status"] == "insufficient_data"
    assert out["mean_absolute_gap"] is None
    assert out["points"] == []


@pytest.mark.asyncio
async def test_rule_reliability_history_unknown_rule_404(db):
    with pytest.raises(Exception) as exc_info:
        await ml.get_rule_reliability_history(rule_id=str(uuid.uuid4()), user=FAKE_USER, db=db)
    assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_rule_reliability_history_no_events(db):
    rule = _rule(db)
    out = await ml.get_rule_reliability_history(rule_id=str(rule.id), user=FAKE_USER, db=db)
    assert out["events"] == []
    assert out["status"] == "insufficient_data"
    assert out["current_theta"] == 1.0  # NULL alpha/beta -> full penalty, never fabricated


@pytest.mark.asyncio
async def test_latency_empty_db(db):
    out = await ml.get_latency(user=FAKE_USER, db=db)
    assert out["sample_size"] == 0
    assert out["status"] == "insufficient_data"
    assert out["p50_duration_ms"] is None
    assert out["by_trigger_source"] == []


@pytest.mark.asyncio
async def test_repeated_patterns_empty_db(db):
    out = await ml.get_repeated_patterns(min_submissions=2, limit=20, user=FAKE_USER, db=db)
    assert out["patterns"] == []
    assert out["status"] == "insufficient_data"


# --------------------------------------------------------------------------
# Populated-DB: the SQL actually computes the right numbers.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_funnel_counts_stages_correctly(db):
    sub = _submission(db)
    check = _check(db, sub)
    rule = _rule(db)
    v_untouched = _violation(db, check, rule_id=rule.id)          # flagged, awaiting review
    v_dismissed = _violation(db, check, rule_id=rule.id)          # feedback, but not scoring
    v_scored = _violation(db, check, rule_id=rule.id)             # feedback + scoring
    v_suppressed = _violation(db, check, suppressed=True)         # excluded entirely
    _feedback(db, v_dismissed, verdict="dismiss", rule_id=None)
    _feedback(db, v_scored, verdict="correct", rule_id=rule.id)

    out = await ml.get_learning_funnel(user=FAKE_USER, db=db)
    assert out["flagged"] == 3
    assert out["feedback_collected"] == 2
    assert out["applied_to_scoring"] == 1
    assert out["awaiting_review"] == 1


@pytest.mark.asyncio
async def test_precision_by_rule_excludes_dismiss(db):
    sub = _submission(db)
    check = _check(db, sub)
    rule = _rule(db, rule_text="X" * 200)
    v1 = _violation(db, check, rule_id=rule.id)
    v2 = _violation(db, check, rule_id=rule.id)
    v3 = _violation(db, check, rule_id=rule.id)
    _feedback(db, v1, verdict="correct", rule_id=rule.id)
    _feedback(db, v2, verdict="not_violation", rule_id=rule.id)
    _feedback(db, v3, verdict="dismiss", rule_id=rule.id)

    out = await ml.get_precision(by="rule", user=FAKE_USER, db=db)
    assert len(out["groups"]) == 1
    g = out["groups"][0]
    assert g["key"] == str(rule.id)
    assert g["correct"] == 1
    assert g["not_violation"] == 1
    assert g["reviewed_total"] == 2  # dismiss excluded from denominator
    assert g["precision"] == 0.5
    assert g["rule_text"].endswith("…")  # truncated at 140 chars


@pytest.mark.asyncio
async def test_precision_by_category(db):
    sub = _submission(db)
    check = _check(db, sub)
    v1 = _violation(db, check, category="seo")
    v2 = _violation(db, check, category="brand")
    _feedback(db, v1, verdict="accept", rule_id=None)   # legacy vocabulary
    _feedback(db, v2, verdict="reject", rule_id=None)

    out = await ml.get_precision(by="category", user=FAKE_USER, db=db)
    by_key = {g["key"]: g for g in out["groups"]}
    assert by_key["seo"]["precision"] == 1.0
    assert by_key["brand"]["precision"] == 0.0


@pytest.mark.asyncio
async def test_calibration_computes_mean_gap(db):
    sub = _submission(db)
    _check(db, sub, overall_score=80.0, reviewer_score=70.0, checked_at=datetime.now(timezone.utc))
    _check(db, sub, overall_score=90.0, reviewer_score=90.0, checked_at=datetime.now(timezone.utc))
    _check(db, sub, overall_score=None, reviewer_score=None)  # ignored: neither score set

    out = await ml.get_calibration(user=FAKE_USER, db=db)
    assert out["sample_size"] == 2
    assert out["status"] == "computed"
    assert out["mean_absolute_gap"] == 5.0
    assert len(out["points"]) == 2


@pytest.mark.asyncio
async def test_rule_reliability_history_returns_events_in_order(db):
    rule = _rule(db, reliability_alpha=Decimal("10.0"), reliability_beta=Decimal("1.0"))
    old = datetime.now(timezone.utc) - timedelta(days=1)
    new = datetime.now(timezone.utc)
    e1 = RuleReliabilityEvent(
        id=uuid.uuid4(), rule_id=rule.id, alpha_before=9.0, beta_before=1.0,
        alpha_after=10.0, beta_after=1.0, theta_before=0.9, theta_after=0.909,
        created_at=old,
    )
    e2 = RuleReliabilityEvent(
        id=uuid.uuid4(), rule_id=rule.id, alpha_before=10.0, beta_before=1.0,
        alpha_after=11.0, beta_after=1.0, theta_before=0.909, theta_after=0.917,
        created_at=new,
    )
    db.add_all([e1, e2])
    db.commit()

    out = await ml.get_rule_reliability_history(rule_id=str(rule.id), user=FAKE_USER, db=db)
    assert out["status"] == "computed"
    assert [e["id"] for e in out["events"]] == [str(e1.id), str(e2.id)]
    assert out["current_theta"] == pytest.approx(10.0 / 11.0)


@pytest.mark.asyncio
async def test_latency_computes_percentiles_and_excludes_incomplete_runs(db):
    sub = _submission(db)
    for ms in (100, 200, 300, 400, 500):
        _run(
            db, sub, status="completed", duration_ms=ms, finished_at=datetime.now(timezone.utc),
            prompt_tokens=10, completion_tokens=5, total_cost_usd=Decimal("0.01"),
            trigger_source="api",
        )
    _run(db, sub, status="running", duration_ms=None)  # still in-flight: excluded
    _run(db, sub, status="failed", duration_ms=9999, finished_at=datetime.now(timezone.utc))  # excluded

    out = await ml.get_latency(user=FAKE_USER, db=db)
    assert out["sample_size"] == 5
    assert out["status"] == "computed"
    assert out["avg_duration_ms"] == 300.0
    assert out["p50_duration_ms"] == 300.0
    assert out["by_trigger_source"] == [{"trigger_source": "api", "count": 5, "avg_duration_ms": 300.0}]


@pytest.mark.asyncio
async def test_repeated_patterns_requires_distinct_submissions(db):
    rule = _rule(db)
    sub1 = _submission(db)
    sub2 = _submission(db)
    check1 = _check(db, sub1)
    check2 = _check(db, sub2)
    _violation(db, check1, rule_id=rule.id)
    _violation(db, check1, rule_id=rule.id)  # same submission — doesn't add to submission_count
    _violation(db, check2, rule_id=rule.id)

    out = await ml.get_repeated_patterns(min_submissions=2, limit=20, user=FAKE_USER, db=db)
    assert out["status"] == "computed"
    assert len(out["patterns"]) == 1
    p = out["patterns"][0]
    assert p["rule_id"] == str(rule.id)
    assert p["submission_count"] == 2
    assert p["violation_count"] == 3


# --------------------------------------------------------------------------
# Reviewer-authored flags (0031) must never enter MODEL-precision math.
# A reviewer-written finding is not a prediction the model made; counting it
# would inflate the exact number used to judge whether the model improves.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reviewer_authored_flag_does_not_move_precision_by_category(db):
    sub = _submission(db)
    check = _check(db, sub)
    v_model = _violation(db, check, category="seo", source="model")
    _feedback(db, v_model, verdict="correct", rule_id=None)

    before = await ml.get_precision(by="category", user=FAKE_USER, db=db)
    assert {g["key"]: g["precision"] for g in before["groups"]}["seo"] == 1.0

    # A reviewer flags text the model missed, and it is later judged wrong.
    # Model precision for 'seo' must be untouched: 1.0, over 1 review.
    v_reviewer = _violation(db, check, category="seo", source="reviewer")
    _feedback(db, v_reviewer, verdict="not_violation", rule_id=None)

    after = await ml.get_precision(by="category", user=FAKE_USER, db=db)
    seo = {g["key"]: g for g in after["groups"]}["seo"]
    assert seo["precision"] == 1.0
    assert seo["reviewed_total"] == 1
    assert seo["not_violation"] == 0


@pytest.mark.asyncio
async def test_reviewer_authored_flag_does_not_move_precision_by_rule(db):
    # Reviewer-authored rows carry rule_id=NULL in practice, which alone would
    # keep them out of the by-rule grouping. Pin a rule_id on purpose so this
    # asserts the source filter itself, not that incidental NULL.
    sub = _submission(db)
    check = _check(db, sub)
    rule = _rule(db)
    v_model = _violation(db, check, rule_id=rule.id, source="model")
    _feedback(db, v_model, verdict="correct", rule_id=rule.id)
    v_reviewer = _violation(db, check, rule_id=rule.id, source="reviewer")
    _feedback(db, v_reviewer, verdict="not_violation", rule_id=rule.id)

    out = await ml.get_precision(by="rule", user=FAKE_USER, db=db)
    g = {x["key"]: x for x in out["groups"]}[str(rule.id)]
    assert g["precision"] == 1.0
    assert g["reviewed_total"] == 1


@pytest.mark.asyncio
async def test_reviewer_authored_flag_does_not_move_precision_by_severity(db):
    sub = _submission(db)
    check = _check(db, sub)
    v_model = _violation(db, check, severity="high", source="model")
    _feedback(db, v_model, verdict="correct", rule_id=None)
    v_reviewer = _violation(db, check, severity="high", source="reviewer")
    _feedback(db, v_reviewer, verdict="not_violation", rule_id=None)

    out = await ml.get_precision(by="severity", user=FAKE_USER, db=db)
    g = {x["key"]: x for x in out["groups"]}["high"]
    assert g["precision"] == 1.0
    assert g["reviewed_total"] == 1


@pytest.mark.asyncio
async def test_funnel_counts_reviewer_authored_separately(db):
    sub = _submission(db)
    check = _check(db, sub)
    rule = _rule(db)
    v_model = _violation(db, check, rule_id=rule.id, source="model")
    _feedback(db, v_model, verdict="correct", rule_id=rule.id)
    v_reviewer = _violation(db, check, source="reviewer")
    _feedback(db, v_reviewer, verdict="correct", rule_id=None)

    out = await ml.get_learning_funnel(user=FAKE_USER, db=db)
    assert out["flagged"] == 1              # the model flagged one thing
    assert out["reviewer_authored"] == 1    # a human flagged the other
    assert out["feedback_collected"] == 1
    assert out["applied_to_scoring"] == 1
    assert out["awaiting_review"] == 0      # never negative, never inflated


@pytest.mark.asyncio
async def test_repeated_patterns_excludes_reviewer_authored(db):
    rule = _rule(db)
    sub1, sub2 = _submission(db), _submission(db)
    check1, check2 = _check(db, sub1), _check(db, sub2)
    _violation(db, check1, rule_id=rule.id, source="model")
    _violation(db, check2, rule_id=rule.id, source="model")
    _violation(db, check2, rule_id=rule.id, source="reviewer")  # not model output

    out = await ml.get_repeated_patterns(min_submissions=2, limit=20, user=FAKE_USER, db=db)
    p = out["patterns"][0]
    assert p["violation_count"] == 2
    assert p["submission_count"] == 2


@pytest.mark.asyncio
async def test_violation_source_defaults_to_model(db):
    """0031's server_default IS the backfill — a row written without `source`
    reads as model-authored, so every pre-migration finding still counts."""
    sub = _submission(db)
    check = _check(db, sub)
    v = _violation(db, check)
    db.refresh(v)
    assert v.source == "model"
    assert v.created_by is None


@pytest.mark.asyncio
async def test_repeated_patterns_below_threshold_is_excluded(db):
    rule = _rule(db)
    sub = _submission(db)
    check = _check(db, sub)
    _violation(db, check, rule_id=rule.id)

    out = await ml.get_repeated_patterns(min_submissions=2, limit=20, user=FAKE_USER, db=db)
    assert out["patterns"] == []
    assert out["status"] == "insufficient_data"
