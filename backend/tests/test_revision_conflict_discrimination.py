"""Which failures are a revision conflict, and which are only being mistaken
for one.

`create_revision` shares one transaction with the audit row, the submission's
current_content mirror and the fix_applied flags. Any of those can violate an
integrity rule for reasons that have nothing to do with concurrency — a user
row deleted mid-request breaking `created_by`, for instance. Reporting those as
`revision_conflict` tells the reviewer another reviewer changed the document
and hands them a "Keep my version" button that can never succeed, while the
real fault stays invisible.

So the handler identifies the race by the constraint that actually decides it,
`uq_submission_revisions_submission_number`, and lets everything else surface
as the server error it is.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes import submissions as submissions_routes
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.schemas.submission import SubmissionRevisionCreate
from tests.test_submission_revisions_and_comments import FakeSession, _User

CONSTRAINT = "uq_submission_revisions_submission_number"


def _save(db, sub, user, **kwargs):
    return asyncio.run(
        submissions_routes.create_revision(
            submission_id=str(sub.id),
            body=SubmissionRevisionCreate(**kwargs),
            user=user,
            db=db,
        )
    )


def _submission(**kwargs):
    base = dict(id=uuid.uuid4(), title="t", content_type="docx", original_content="orig")
    base.update(kwargs)
    return Submission(**base)


class _Diag:
    def __init__(self, constraint_name):
        self.constraint_name = constraint_name


class _PgError(Exception):
    """Stands in for psycopg2's error object, which carries the SQLSTATE and
    the violated constraint by name — the exact signal the handler prefers."""

    def __init__(self, message, constraint_name=None, pgcode="23505"):
        super().__init__(message)
        self.pgcode = pgcode
        if constraint_name is not None:
            self.diag = _Diag(constraint_name)


def _integrity(message, **kw):
    return IntegrityError(message, None, _PgError(message, **kw))


# --- the discriminator itself ----------------------------------------------

def test_named_revision_constraint_is_the_race():
    exc = _integrity("duplicate key", constraint_name=CONSTRAINT)
    assert submissions_routes._is_duplicate_revision_number(exc) is True


def test_a_different_named_constraint_is_not():
    exc = _integrity("duplicate key", constraint_name="uq_users_email")
    assert submissions_routes._is_duplicate_revision_number(exc) is False


def test_a_foreign_key_violation_is_not():
    # 23503 = foreign_key_violation. A deleted user breaking created_by, say.
    exc = _integrity(
        'insert or update on table "submission_revisions" violates foreign key constraint',
        constraint_name="submission_revisions_created_by_fkey",
        pgcode="23503",
    )
    assert submissions_routes._is_duplicate_revision_number(exc) is False


def test_it_falls_back_to_the_constraint_name_in_the_text():
    # A driver that exposes no diagnostics still names the constraint in the
    # message. That is the specific constraint, not a guess at wording.
    exc = IntegrityError(
        f'duplicate key value violates unique constraint "{CONSTRAINT}"',
        None,
        Exception(f'duplicate key value violates unique constraint "{CONSTRAINT}"'),
    )
    assert submissions_routes._is_duplicate_revision_number(exc) is True


def test_an_unnamed_unique_violation_elsewhere_is_not_the_race():
    exc = IntegrityError("duplicate key", None, Exception("some other unique index"))
    assert submissions_routes._is_duplicate_revision_number(exc) is False


# --- how the route behaves on each --------------------------------------

class _FailingSession(FakeSession):
    def __init__(self, error):
        super().__init__()
        self._error = error
        self.armed = False
        self.rollbacks = 0
        self._pending: list = []

    def add(self, obj):
        if obj not in self._pending:
            self._pending.append(obj)
        super().add(obj)

    def commit(self):
        if self.armed:
            self.armed = False
            raise self._error
        self._pending.clear()
        super().commit()

    def rollback(self):
        self.rollbacks += 1
        for obj in self._pending:
            rows = self.rows_for(type(obj))
            if obj in rows:
                rows.remove(obj)
        self._pending.clear()


def test_the_revision_race_becomes_409():
    db = _FailingSession(_integrity("duplicate key", constraint_name=CONSTRAINT))
    sub = _submission()
    db.add(sub)
    db.commit()

    db.armed = True
    with pytest.raises(HTTPException) as raised:
        _save(db, sub, _User(), content="mine", source="manual_edit", expected_revision=0)

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "revision_conflict"
    assert db.rollbacks == 1


def test_an_unrelated_integrity_error_is_not_dressed_up_as_a_conflict():
    db = _FailingSession(
        _integrity("fk violation", constraint_name="submission_revisions_created_by_fkey", pgcode="23503")
    )
    sub = _submission()
    db.add(sub)
    db.commit()

    db.armed = True
    # It surfaces as itself — a 500 to the client — rather than claiming a
    # second reviewer that does not exist.
    with pytest.raises(IntegrityError):
        _save(db, sub, _User(), content="mine", source="manual_edit", expected_revision=0)

    # Still rolled back: the transaction is dead either way.
    assert db.rollbacks == 1
    assert db.rows_for(SubmissionRevision) == []
