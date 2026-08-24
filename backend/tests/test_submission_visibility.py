"""Bucket-scoped visibility.

A reviewer sees a submission when it is assigned to them or they uploaded it.
Anything else is 404 — deliberately not 403, because a 403 confirms the
document exists, which is itself a disclosure in a compliance tool.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.auth.visibility import (
    get_visible_submission,
    may_see_submission,
    visible_submission_filter,
)
from app.models.review_assignment import ReviewAssignment
from app.models.submission import Submission
from tests.support.fake_session import FakeSession


class _User:
    def __init__(self, role="user"):
        self.id = uuid.uuid4()
        self.role = role


@pytest.fixture
def db():
    return FakeSession()


def _submission(db, submitted_by=None):
    sub = Submission(id=uuid.uuid4(), title="Brochure", content_type="docx",
                     submitted_by=submitted_by)
    db.add(sub)
    return sub


def test_admin_sees_everything(db):
    sub = _submission(db)
    assert may_see_submission(db, sub, _User("admin")) is True


def test_super_admin_sees_everything(db):
    sub = _submission(db)
    assert may_see_submission(db, sub, _User("super_admin")) is True


def test_reviewer_sees_their_own_upload(db):
    reviewer = _User("user")
    sub = _submission(db, submitted_by=reviewer.id)
    assert may_see_submission(db, sub, reviewer) is True


def test_reviewer_sees_a_document_assigned_to_them(db):
    reviewer = _User("user")
    sub = _submission(db)
    db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=sub.id,
                            assignee_id=reviewer.id, status="open"))
    assert may_see_submission(db, sub, reviewer) is True


def test_reviewer_still_sees_it_after_the_assignment_closes(db):
    """Their own history must not vanish the moment an admin signs off."""
    reviewer = _User("user")
    sub = _submission(db)
    db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=sub.id,
                            assignee_id=reviewer.id, status="closed"))
    assert may_see_submission(db, sub, reviewer) is True


def test_reviewer_cannot_see_someone_elses_document(db):
    sub = _submission(db, submitted_by=_User("user").id)
    db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=sub.id,
                            assignee_id=_User("user").id, status="open"))
    assert may_see_submission(db, sub, _User("user")) is False


def test_reviewer_cannot_see_an_unassigned_legacy_document(db):
    """Pre-existing rows have submitted_by = NULL and no assignment. They
    belong to the admin's Unassigned queue until triaged."""
    sub = _submission(db, submitted_by=None)
    assert may_see_submission(db, sub, _User("user")) is False


def test_an_assignment_on_a_different_document_grants_nothing(db):
    """Guards against matching on assignee alone and ignoring submission_id."""
    reviewer = _User("user")
    mine = _submission(db)
    theirs = _submission(db)
    db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=mine.id,
                            assignee_id=reviewer.id, status="open"))
    assert may_see_submission(db, theirs, reviewer) is False


def test_anonymous_caller_sees_nothing(db):
    class _Anon:
        id = None
        role = "user"

    assert may_see_submission(db, _submission(db), _Anon()) is False


def test_hidden_submission_raises_404_not_403(db):
    sub = _submission(db)
    with pytest.raises(HTTPException) as exc:
        get_visible_submission(db, sub.id, _User("user"))
    assert exc.value.status_code == 404


def test_missing_submission_raises_404(db):
    with pytest.raises(HTTPException) as exc:
        get_visible_submission(db, uuid.uuid4(), _User("admin"))
    assert exc.value.status_code == 404


def test_visible_submission_returns_the_row(db):
    reviewer = _User("user")
    sub = _submission(db, submitted_by=reviewer.id)
    assert get_visible_submission(db, sub.id, reviewer) is sub


def test_filter_is_none_for_admin():
    """None means "no restriction" — the caller adds no WHERE clause."""
    assert visible_submission_filter(_User("admin")) is None
    assert visible_submission_filter(_User("super_admin")) is None


def test_filter_is_present_for_a_reviewer():
    assert visible_submission_filter(_User("user")) is not None
