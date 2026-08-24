"""record_sync — the transactional half of the audit service.

The async `record` is best-effort by design: its own session, its own commit,
every exception swallowed. That is the right trade for telemetry and the wrong
one for a compliance trail, where a missing row is indistinguishable from an
action that never happened. record_sync writes into the CALLER's session and
does not commit, so the event rides the caller's transaction.
"""
import uuid

import pytest

from app.models.audit_event import AuditEvent
from app.services.observability import audit
from tests.support.fake_session import FakeSession


class _Actor:
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role


def test_writes_the_event_into_the_callers_session():
    db = FakeSession()
    actor = _Actor()

    audit.record_sync(db, "assignment_created", actor=actor,
                      target_type="assignment", target_id="a1")

    rows = db.query(AuditEvent).all()
    assert len(rows) == 1
    assert rows[0].event_type == "assignment_created"
    assert rows[0].actor_user_id == str(actor.id)
    assert rows[0].actor_role == "admin"


def test_does_not_commit():
    """The caller's commit carries it. Committing here would defeat the point:
    the event would survive a rolled-back mutation."""
    db = FakeSession()
    audit.record_sync(db, "assignment_created", actor=_Actor())
    assert db.commits == 0


def test_records_the_document_scope():
    db = FakeSession()
    sub_id = uuid.uuid4()

    audit.record_sync(db, "violation_dismissed", actor=_Actor(),
                      target_type="violation", target_id="v1",
                      scope_submission_id=sub_id)

    assert db.query(AuditEvent).all()[0].scope_submission_id == sub_id


def test_coerces_unserializable_payloads():
    db = FakeSession()
    audit.record_sync(db, "submission_edited", actor=_Actor(),
                      before={"at": uuid.uuid4()})
    # round-tripped through json with default=str rather than dropped
    assert isinstance(db.query(AuditEvent).all()[0].before_state["at"], str)


def test_raises_rather_than_swallowing():
    """Unlike `record`, a failure here must surface so the caller's transaction
    rolls back. A silently dropped compliance event is worse than a 500."""
    class Exploding(FakeSession):
        def add(self, obj):
            raise RuntimeError("db is down")

    with pytest.raises(RuntimeError):
        audit.record_sync(Exploding(), "assignment_created", actor=_Actor())


def test_actor_is_optional():
    db = FakeSession()
    audit.record_sync(db, "system_event")
    row = db.query(AuditEvent).all()[0]
    assert row.actor_user_id is None
    assert row.actor_role is None
