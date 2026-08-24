"""Assembling the trail.

The events and the diffs live in different tables on purpose: audit_events says
what someone did, submission_revisions holds the text. The service joins them so
an edit renders as an actual before/after rather than a JSON blob.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_event import AuditEvent
from app.models.review_assignment import ReviewAssignment
from app.models.submission_revision import SubmissionRevision
from app.services import trail_service
from tests.support.fake_session import FakeSession

T0 = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    return FakeSession()


def _event(db, sub_id, event_type, at, actor_id=None, **kw):
    e = AuditEvent(
        id=uuid.uuid4(), event_type=event_type, created_at=at,
        scope_submission_id=sub_id,
        actor_user_id=str(actor_id) if actor_id else None,
        **kw,
    )
    db.add(e)
    return e


def test_document_trail_is_newest_first(db):
    sub = uuid.uuid4()
    _event(db, sub, "assignment_created", T0)
    _event(db, sub, "submission_edited", T0 + timedelta(hours=1))
    _event(db, sub, "submission_approved", T0 + timedelta(hours=2))

    rows = trail_service.document_trail(db, sub)

    assert [r["event_type"] for r in rows] == [
        "submission_approved", "submission_edited", "assignment_created",
    ]


def test_document_trail_excludes_other_documents(db):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    _event(db, mine, "submission_edited", T0)
    _event(db, theirs, "submission_edited", T0)

    assert len(trail_service.document_trail(db, mine)) == 1


def test_edit_events_carry_a_diff(db):
    sub = uuid.uuid4()
    db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub, revision_number=1,
                              content="guaranteed returns of 8%", source="manual_edit"))
    db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub, revision_number=2,
                              content="returns of up to 8%*", source="manual_edit"))
    _event(db, sub, "submission_edited", T0, metadata_={"revision_number": 2})

    row = trail_service.document_trail(db, sub)[0]

    assert row["diff"] is not None
    assert "guaranteed" in row["diff"]["before"]
    assert "up to" in row["diff"]["after"]


def test_first_revision_diffs_against_empty(db):
    sub = uuid.uuid4()
    db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub, revision_number=1,
                              content="first draft", source="manual_edit"))
    _event(db, sub, "submission_edited", T0, metadata_={"revision_number": 1})

    row = trail_service.document_trail(db, sub)[0]

    assert row["diff"]["before"] == ""
    assert row["diff"]["after"] == "first draft"


def test_non_edit_events_have_no_diff(db):
    sub = uuid.uuid4()
    _event(db, sub, "assignment_created", T0)
    assert trail_service.document_trail(db, sub)[0]["diff"] is None


def test_edit_event_without_a_revision_number_degrades_gracefully(db):
    """Events written before this feature carry no revision_number. They must
    still render, without a diff, rather than raising."""
    sub = uuid.uuid4()
    _event(db, sub, "submission_edited", T0, metadata_=None)
    assert trail_service.document_trail(db, sub)[0]["diff"] is None


def test_edit_event_naming_a_missing_revision_degrades_gracefully(db):
    """A purged or never-written revision must not take the whole trail down."""
    sub = uuid.uuid4()
    _event(db, sub, "submission_edited", T0, metadata_={"revision_number": 99})

    row = trail_service.document_trail(db, sub)[0]

    assert row["diff"]["before"] == ""
    assert row["diff"]["after"] == ""


def test_reviewer_trail_returns_only_that_actors_events(db):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    _event(db, uuid.uuid4(), "submission_edited", T0, actor_id=mine)
    _event(db, uuid.uuid4(), "submission_edited", T0, actor_id=theirs)

    out = trail_service.reviewer_trail(db, mine)

    assert len(out["activity"]) == 1


def test_reviewer_trail_respects_the_limit(db):
    reviewer = uuid.uuid4()
    for i in range(5):
        _event(db, uuid.uuid4(), "submission_edited", T0 + timedelta(minutes=i),
               actor_id=reviewer)

    assert len(trail_service.reviewer_trail(db, reviewer, limit=2)["activity"]) == 2


def test_reviewer_stats_count_assignments(db):
    reviewer = uuid.uuid4()
    for status in ("open", "in_review", "closed", "closed"):
        db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=uuid.uuid4(),
                                assignee_id=reviewer, status=status))

    stats = trail_service.reviewer_trail(db, reviewer)["stats"]

    assert stats["open"] == 2      # open + in_review are both still on the plate
    assert stats["closed"] == 2
    assert stats["total"] == 4
