"""REASON_TO_QUEUE routing (POST /compliance/violations/{id}/actions) and
RuleFeedbackService.apply_action — the reviewer-action taxonomy
(correct/not_violation/dismiss) that replaces the 100%-client-side Dismiss
button (a live silent-discard bug, see architecture plan) and delegates
weight math to apply_feedback's existing accept/reject vocabulary unchanged.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.sql.elements import Null

from app.api.routes.compliance import REASON_TO_QUEUE, resolve_routed_queue
from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.violation import Violation
from app.services.rule_feedback_service import RuleFeedbackService


# --------------------------------------------------------------------------
# REASON_TO_QUEUE routing
# --------------------------------------------------------------------------

def test_known_reasons_route_to_their_queue():
    assert resolve_routed_queue("wrong_severity") == "needs_severity_review"
    assert resolve_routed_queue("out_of_scope") == "needs_legal_review"
    assert resolve_routed_queue("duplicate") == "needs_dedup_review"


def test_unmapped_reason_routes_nowhere():
    assert resolve_routed_queue("just_because") is None


def test_no_reason_routes_nowhere():
    assert resolve_routed_queue(None) is None
    assert resolve_routed_queue("") is None


def test_every_configured_reason_maps_to_a_real_queue_name():
    # Catches a typo'd empty-string/None entry silently swallowing a reason.
    assert REASON_TO_QUEUE
    assert all(REASON_TO_QUEUE.values())


# --------------------------------------------------------------------------
# RuleFeedbackService.apply_action — minimal in-memory fake session
# --------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._predicates = []

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    def _rows(self):
        return self._session.rows_for(self._model)

    def _matches(self, obj):
        return all(getattr(obj, key, None) == val for key, val in self._predicates)

    def first(self):
        for obj in self._rows():
            if self._matches(obj):
                return obj
        return None

    def all(self):
        return [o for o in self._rows() if self._matches(o)]


class FakeSession:
    """Minimal in-memory stand-in for a SQLAlchemy Session. Real enough to
    exercise RuleFeedbackService's query/add/delete/commit calls without the
    Postgres-only JSONB/UUID column types breaking on sqlite."""

    def __init__(self):
        self._store: dict = {}
        self.commits = 0

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, model):
        return _FakeQuery(self, model)

    def add(self, obj):
        rows = self.rows_for(type(obj))
        if obj not in rows:
            rows.append(obj)

    def delete(self, obj):
        rows = self.rows_for(type(obj))
        if obj in rows:
            rows.remove(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


def _violation(rule_id=None) -> Violation:
    return Violation(
        id=uuid.uuid4(), category="disclosure", severity="high",
        description="d", current_text="flagged text", suggested_fix="fix text",
        confidence=0.8, rule_id=rule_id,
    )


def _rule(**overrides) -> Rule:
    rule = Rule(id=uuid.uuid4(), reliability_alpha=Decimal("9.0"), reliability_beta=Decimal("1.0"))
    for k, v in overrides.items():
        setattr(rule, k, v)
    return rule


def test_dismiss_persists_a_row_and_touches_no_weights():
    db = FakeSession()
    v = _violation()
    db.add(v)

    result = RuleFeedbackService.apply_action(db, v.id, "dismiss", reason="duplicate")

    assert result["weight_updated"] is False
    assert result["reliability"] is None
    assert result["routed_queue"] is None
    assert v.review_status == "actioned"
    assert v.resolved_at is not None
    rows = db.rows_for(RuleFeedback)
    assert len(rows) == 1
    assert rows[0].verdict == "dismiss"
    assert rows[0].reason == "duplicate"


def test_correct_updates_rule_reliability_and_stores_real_taxonomy_verdict():
    rule = _rule()
    db = FakeSession()
    db.add(rule)
    v = _violation(rule_id=rule.id)
    db.add(v)

    result = RuleFeedbackService.apply_action(db, v.id, "correct")

    assert result["weight_updated"] is True
    assert rule.reliability_alpha == Decimal("10.0")  # alpha += 1
    fb = db.rows_for(RuleFeedback)[0]
    assert fb.verdict == "correct"  # the real taxonomy value, not apply_feedback's internal 'accept'


def test_not_violation_reverts_a_prior_correct_and_applies_reject():
    rule = _rule()
    db = FakeSession()
    db.add(rule)
    v = _violation(rule_id=rule.id)
    db.add(v)

    RuleFeedbackService.apply_action(db, v.id, "correct")
    assert rule.reliability_alpha == Decimal("10.0")

    RuleFeedbackService.apply_action(db, v.id, "not_violation")

    # Reverts the earlier 'correct' (alpha -1) then applies 'not_violation' (beta +1).
    assert rule.reliability_alpha == Decimal("9.0")
    assert rule.reliability_beta == Decimal("2.0")
    fb = db.rows_for(RuleFeedback)[0]
    assert fb.verdict == "not_violation"


def test_dismiss_then_correct_does_not_crash_and_applies_a_fresh_weight():
    # Regression: apply_feedback's own revert math only understands
    # accept/reject previous verdicts. Without normalizing a prior 'dismiss'
    # row first, apply_feedback would raise ValueError on it.
    rule = _rule()
    db = FakeSession()
    db.add(rule)
    v = _violation(rule_id=rule.id)
    db.add(v)

    RuleFeedbackService.apply_action(db, v.id, "dismiss")
    assert rule.reliability_alpha == Decimal("9.0")  # untouched by dismiss

    result = RuleFeedbackService.apply_action(db, v.id, "correct")

    assert result["weight_updated"] is True
    assert rule.reliability_alpha == Decimal("10.0")  # fresh +1, nothing spuriously reverted
    assert len(db.rows_for(RuleFeedback)) == 1  # still one row for this (violation, reviewer)


def test_reason_routes_to_a_queue_and_is_persisted():
    db = FakeSession()
    v = _violation()
    db.add(v)

    result = RuleFeedbackService.apply_action(
        db, v.id, "not_violation", reason="wrong_severity",
        routed_queue=resolve_routed_queue("wrong_severity"),
    )

    assert result["routed_queue"] == "needs_severity_review"
    fb = db.rows_for(RuleFeedback)[0]
    assert fb.routed_queue == "needs_severity_review"
    assert fb.reason == "wrong_severity"


def test_unknown_action_raises_value_error():
    db = FakeSession()
    v = _violation()
    db.add(v)
    with pytest.raises(ValueError):
        RuleFeedbackService.apply_action(db, v.id, "bogus")


def test_unknown_violation_raises_value_error():
    db = FakeSession()
    with pytest.raises(ValueError):
        RuleFeedbackService.apply_action(db, uuid.uuid4(), "dismiss")
