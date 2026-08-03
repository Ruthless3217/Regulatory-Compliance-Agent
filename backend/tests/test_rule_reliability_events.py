"""rule_reliability_events — the append-only before/after audit trail behind
`rules.reliability_alpha/beta` (migration 0029).

Those two columns are mutated IN PLACE on every verdict, so without this row
the prior value is unrecoverable and GET /model-learning/rule-reliability-history
can only ever report insufficient_data. These tests pin that exactly one event
is written per weight-changing verdict, that before/after are captured on the
right side of the mutation, and that a dismiss (which touches no weight)
writes none.
"""
import uuid
from decimal import Decimal

from sqlalchemy.sql.elements import Null

from app.models.rule import Rule
from app.models.rule_feedback import RuleFeedback
from app.models.rule_reliability_event import RuleReliabilityEvent
from app.models.violation import Violation
from app.services.agents.compliance.reliability import theta
from app.services.rule_feedback_service import RuleFeedbackService


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

    def _matches(self, obj):
        return all(getattr(obj, key, None) == val for key, val in self._predicates)

    def first(self):
        for obj in self._session.rows_for(self._model):
            if self._matches(obj):
                return obj
        return None

    def all(self):
        return [o for o in self._session.rows_for(self._model) if self._matches(o)]


class FakeSession:
    """As in test_reviewer_actions.py, but flush() assigns a PK the way real
    SQLAlchemy does — the service flushes specifically to obtain
    rule_feedback.id for the event's FK, so a no-op flush would let the test
    pass while the real linkage silently stayed NULL."""

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
        for rows in self._store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    def commit(self):
        self.commits += 1


def _violation(rule_id=None) -> Violation:
    return Violation(
        id=uuid.uuid4(), category="disclosure", severity="high",
        description="d", current_text="flagged text", suggested_fix="fix text",
        confidence=0.8, rule_id=rule_id,
    )


def _rule(alpha="9.0", beta="1.0") -> Rule:
    return Rule(
        id=uuid.uuid4(),
        reliability_alpha=Decimal(alpha),
        reliability_beta=Decimal(beta),
    )


def _events(db):
    return db.rows_for(RuleReliabilityEvent)


# --- the write happens at all -------------------------------------------------

def test_accept_writes_exactly_one_reliability_event():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "accept")

    assert len(_events(db)) == 1


def test_reject_also_writes_an_event():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "reject")

    assert len(_events(db)) == 1


# --- before/after captured on the right side of the mutation -----------------

def test_before_values_are_the_pre_mutation_state():
    db = FakeSession()
    rule = _rule(alpha="9.0", beta="1.0")
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "accept")
    ev = _events(db)[0]

    # before == the values the rule held going in, NOT the updated ones
    assert float(ev.alpha_before) == 9.0
    assert float(ev.beta_before) == 1.0
    # after == what the rule now holds
    assert float(ev.alpha_after) == float(rule.reliability_alpha)
    assert float(ev.beta_after) == float(rule.reliability_beta)
    # and the mutation actually moved something, else this test proves nothing
    assert (ev.alpha_before, ev.beta_before) != (ev.alpha_after, ev.beta_after)


def test_theta_matches_the_alpha_beta_pair_on_each_side():
    db = FakeSession()
    rule = _rule(alpha="9.0", beta="1.0")
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "reject")
    ev = _events(db)[0]

    assert ev.theta_before == theta(ev.alpha_before, ev.beta_before)
    assert ev.theta_after == theta(ev.alpha_after, ev.beta_after)


def test_reject_lowers_theta_and_accept_raises_it():
    db = FakeSession()
    rule = _rule(alpha="5.0", beta="5.0")
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)
    RuleFeedbackService.apply_feedback(db, v.id, "reject")
    assert _events(db)[0].theta_after < _events(db)[0].theta_before

    db2 = FakeSession()
    rule2 = _rule(alpha="5.0", beta="5.0")
    v2 = _violation(rule_id=rule2.id)
    db2.add(rule2)
    db2.add(v2)
    RuleFeedbackService.apply_feedback(db2, v2.id, "accept")
    assert _events(db2)[0].theta_after > _events(db2)[0].theta_before


# --- linkage ------------------------------------------------------------------

def test_event_links_to_the_rule_and_the_feedback_row():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "accept")
    ev = _events(db)[0]
    feedback = db.rows_for(RuleFeedback)[0]

    assert ev.rule_id == rule.id
    assert ev.rule_feedback_id is not None, "FK left NULL — the flush-for-PK broke"
    assert ev.rule_feedback_id == feedback.id


# --- cases that must NOT write ------------------------------------------------

def test_dismiss_writes_no_event():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_action(db, v.id, "dismiss", reason="duplicate")

    assert _events(db) == []


def test_violation_with_no_rule_writes_no_event():
    # precedent/novel-tier findings carry no rule_id — verdict is recorded for
    # evaluation but no weight moves, so there is no before/after to log.
    db = FakeSession()
    v = _violation(rule_id=None)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "accept")

    assert _events(db) == []


# --- the taxonomy path also logs ---------------------------------------------

def test_correct_action_writes_an_event():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_action(db, v.id, "correct")

    assert len(_events(db)) == 1


def test_not_violation_action_writes_an_event():
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_action(db, v.id, "not_violation")

    assert len(_events(db)) == 1


def test_resubmitting_a_flipped_verdict_appends_a_second_event():
    # The audit row is upserted (one verdict per reviewer), but the history is
    # append-only: both the original and the correction must remain visible.
    db = FakeSession()
    rule = _rule()
    v = _violation(rule_id=rule.id)
    db.add(rule)
    db.add(v)

    RuleFeedbackService.apply_feedback(db, v.id, "accept")
    RuleFeedbackService.apply_feedback(db, v.id, "reject")

    assert len(_events(db)) == 2
    assert len(db.rows_for(RuleFeedback)) == 1, "audit row should upsert, not duplicate"
