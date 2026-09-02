"""Assignment lifecycle.

open -> in_review -> awaiting_signoff -> closed, with send-back returning
awaiting_signoff to in_review, and reassignment closing the old row while
opening a new one.

The partial unique index cannot run here (no Postgres in the suite), so the
duplicate-assignment guard is asserted through the service. The index remains
the production backstop for a genuine concurrent race.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.audit_event import AuditEvent
from app.models.user import User
from app.services import assignment_service as svc
from tests.support.fake_session import FakeSession

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def _user(db, role="admin"):
    """A real, active User row registered in `db` — assign()/reassign() look
    the assignee up against the users table."""
    u = User(id=uuid.uuid4(), role=role, is_active=True,
             username=f"u-{uuid.uuid4().hex[:8]}")
    db.add(u)
    return u


@pytest.fixture
def db():
    return FakeSession()


@pytest.fixture
def admin(db):
    return _user(db, "admin")


@pytest.fixture
def reviewer(db):
    return _user(db, "user")


def test_assign_creates_an_open_assignment(db, admin, reviewer):
    sub_id = uuid.uuid4()

    a = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id,
                   actor=admin, priority="high", note="check the disclaimers")

    assert a.status == "open"
    assert a.submission_id == sub_id
    assert a.assignee_id == reviewer.id
    assert a.assigned_by == admin.id
    assert a.priority == "high"
    assert a.note == "check the disclaimers"


def test_assign_emits_an_audit_event(db, admin, reviewer):
    sub_id = uuid.uuid4()
    svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)

    events = db.query(AuditEvent).all()
    assert [e.event_type for e in events] == ["assignment_created"]
    assert events[0].scope_submission_id == sub_id


def test_second_active_assignment_is_refused(db, admin, reviewer):
    sub_id = uuid.uuid4()
    svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)

    with pytest.raises(svc.ActiveAssignmentExists):
        svc.assign(db, submission_id=sub_id, assignee_id=_user(db, "user").id, actor=admin)


def test_a_closed_assignment_frees_the_submission(db, admin, reviewer):
    """The uniqueness rule is about ACTIVE assignments — a document must be
    assignable again after its previous assignment closed."""
    sub_id = uuid.uuid4()
    first = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=first, actor=reviewer)
    svc.complete(db, assignment=first, actor=reviewer)
    svc.close(db, assignment=first, actor=admin, outcome="approved")

    second = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)
    assert second.status == "open"


def test_start_moves_to_in_review_and_stamps_started_at(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)

    svc.start(db, assignment=a, actor=reviewer)

    assert a.status == "in_review"
    assert a.started_at is not None


def test_only_the_assignee_may_start(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)

    with pytest.raises(svc.NotAssignee):
        svc.start(db, assignment=a, actor=_user(db, "user"))


def test_complete_moves_to_awaiting_signoff(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=a, actor=reviewer)

    svc.complete(db, assignment=a, actor=reviewer)

    assert a.status == "awaiting_signoff"
    assert a.completed_at is not None


def test_send_back_returns_to_in_review_and_keeps_the_reviewer(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=a, actor=reviewer)
    svc.complete(db, assignment=a, actor=reviewer)

    svc.send_back(db, assignment=a, actor=admin, reason="disclaimer still missing")

    assert a.status == "in_review"
    assert a.assignee_id == reviewer.id  # ownership is unchanged
    assert a.outcome_note == "disclaimer still missing"


def test_send_back_requires_a_reason(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=a, actor=reviewer)
    svc.complete(db, assignment=a, actor=reviewer)

    with pytest.raises(ValueError):
        svc.send_back(db, assignment=a, actor=admin, reason="   ")


def test_close_records_outcome_and_closer(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=a, actor=reviewer)
    svc.complete(db, assignment=a, actor=reviewer)

    svc.close(db, assignment=a, actor=admin, outcome="approved")

    assert a.status == "closed"
    assert a.outcome == "approved"
    assert a.closed_by == admin.id
    assert a.closed_at is not None


def test_reassign_supersedes_the_old_row_and_chains_it(db, admin, reviewer):
    sub_id = uuid.uuid4()
    other = _user(db, "user")
    first = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)

    second = svc.reassign(db, assignment=first, new_assignee_id=other.id, actor=admin)

    assert first.status == "superseded"
    assert first.outcome == "superseded"
    assert first.superseded_by == second.id
    assert second.id is not None
    assert second.status == "open"
    assert second.assignee_id == other.id
    assert second.submission_id == sub_id


def test_reassign_leaves_exactly_one_active_row(db, admin, reviewer):
    """The whole point of superseding rather than overwriting: the partial
    unique index must still see a single active assignment afterwards."""
    sub_id = uuid.uuid4()
    first = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)
    svc.reassign(db, assignment=first, new_assignee_id=_user(db, "user").id, actor=admin)

    assert svc.active_for_submission(db, sub_id) is not None


def test_reassign_carries_over_due_and_priority(db, admin, reviewer):
    first = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id,
                       actor=admin, priority="urgent", due_at=NOW)

    second = svc.reassign(db, assignment=first, new_assignee_id=_user(db, "user").id, actor=admin)

    assert second.priority == "urgent"
    assert second.due_at == NOW


def test_illegal_transition_is_refused(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
    svc.start(db, assignment=a, actor=reviewer)
    svc.complete(db, assignment=a, actor=reviewer)
    svc.close(db, assignment=a, actor=admin, outcome="approved")

    with pytest.raises(svc.IllegalTransition):
        svc.start(db, assignment=a, actor=reviewer)


def test_terminal_states_have_no_exits():
    for terminal in ("closed", "superseded", "cancelled"):
        assert svc.ALLOWED_TRANSITIONS[terminal] == set()


def test_unknown_priority_is_refused(db, admin, reviewer):
    with pytest.raises(ValueError):
        svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id,
                   actor=admin, priority="catastrophic")


def test_cancel_is_available_from_every_active_state(db, admin, reviewer):
    for setup in ("open", "in_review", "awaiting_signoff"):
        a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
        if setup in ("in_review", "awaiting_signoff"):
            svc.start(db, assignment=a, actor=reviewer)
        if setup == "awaiting_signoff":
            svc.complete(db, assignment=a, actor=reviewer)

        svc.cancel(db, assignment=a, actor=admin, reason="withdrawn")
        assert a.status == "cancelled"


def test_close_is_available_from_every_active_state(db, admin, reviewer):
    """Sign-off ends the work wherever the reviewer had got to.

    Restricting close to `awaiting_signoff` was what stranded assignments: an
    admin approving a document the reviewer never marked complete left the row
    active forever, and the partial unique index kept the submission locked to
    it. Terminal states are still terminal — see test_terminal_states_have_no_exits.
    """
    for setup in ("open", "in_review", "awaiting_signoff"):
        a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
        if setup in ("in_review", "awaiting_signoff"):
            svc.start(db, assignment=a, actor=reviewer)
        if setup == "awaiting_signoff":
            svc.complete(db, assignment=a, actor=reviewer)

        svc.close(db, assignment=a, actor=admin, outcome="approved")

        assert a.status == "closed", f"close refused from {setup!r}"
        assert a.outcome == "approved"
        assert a.closed_at is not None


def test_close_refuses_an_unknown_outcome(db, admin, reviewer):
    a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)

    with pytest.raises(ValueError):
        svc.close(db, assignment=a, actor=admin, outcome="blessed")

    assert a.status == "open", "a refused close must not move the assignment"


def test_close_emits_an_audit_event_scoped_to_the_document(db, admin, reviewer):
    sub_id = uuid.uuid4()
    a = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)

    svc.close(db, assignment=a, actor=admin, outcome="approved", note="signed off")

    event = [e for e in db.rows_for(AuditEvent) if e.event_type == "assignment_closed"][-1]
    assert event.scope_submission_id == sub_id
    assert event.after_state == {"status": "closed", "outcome": "approved"}
