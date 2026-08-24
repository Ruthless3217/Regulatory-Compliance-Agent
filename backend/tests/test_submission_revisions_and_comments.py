"""POST/GET /submissions/{id}/revisions(/{n}) and POST/GET/PATCH/DELETE
/submissions/{id}/comments — the content-versioning mutation primitive
(manual_edit/apply_fix/bulk_apply_fixes/restore, UNIQUE(submission_id,
revision_number)) and the freestanding-comment CRUD, mirroring
comparisons.py's annotation shape.

Uses a minimal in-memory fake Session (same trick as test_reviewer_actions.py)
since the models use Postgres-only UUID/ARRAY column types that don't survive
a real sqlite session — calls the route functions directly (matching
test_run_tracker_staleness.py's asyncio.run(...) style) rather than spinning
up a TestClient + DB.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.sql.elements import Null

from app.api.routes import submissions as submissions_routes
from app.models.document_comment import DocumentComment
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.schemas.submission import (
    DocumentCommentCreate,
    DocumentCommentUpdate,
    SubmissionRevisionCreate,
)


class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._predicates = []
        self._order_key = None

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    def order_by(self, col):
        self._order_key = col.key
        return self

    def _matches(self, obj):
        for key, val in self._predicates:
            actual = getattr(obj, key, None)
            # Emulate Postgres' UUID<->str coercion: route handlers compare a
            # raw path-param string against a UUID column value.
            if str(actual) != str(val):
                return False
        return True

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            rows.sort(key=lambda o: getattr(o, self._order_key, None) or 0)
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()


class FakeSession:
    """Minimal in-memory stand-in for a SQLAlchemy Session — see
    test_reviewer_actions.py's FakeSession for the original."""

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

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


class _User:
    # Admin: these tests cover revisions and comments, not visibility, and a
    # plain `user` now 404s on a submission that is neither theirs nor
    # assigned. test_lexical_revision.py imports this class too.
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role


def _submission() -> Submission:
    return Submission(id=uuid.uuid4(), title="t", content_type="text", original_content="orig")


# ---------------------------------------------------------------------------
# _next_revision_number
# ---------------------------------------------------------------------------

def test_next_revision_number_starts_at_one():
    db = FakeSession()
    assert submissions_routes._next_revision_number(db, uuid.uuid4()) == 1


def test_next_revision_number_increments_from_existing():
    db = FakeSession()
    sub_id = uuid.uuid4()
    for n in (1, 2, 3):
        db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub_id, revision_number=n, content="x", source="manual_edit"))
    assert submissions_routes._next_revision_number(db, sub_id) == 4


def test_next_revision_number_is_scoped_per_submission():
    db = FakeSession()
    sub_a, sub_b = uuid.uuid4(), uuid.uuid4()
    db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub_a, revision_number=1, content="x", source="manual_edit"))
    db.add(SubmissionRevision(id=uuid.uuid4(), submission_id=sub_a, revision_number=2, content="x", source="manual_edit"))
    # sub_b has no revisions yet — must not see sub_a's count.
    assert submissions_routes._next_revision_number(db, sub_b) == 1


def test_revision_source_rejects_unknown_value():
    with pytest.raises(ValidationError):
        SubmissionRevisionCreate(content="x", source="bogus")


# ---------------------------------------------------------------------------
# create_revision / list_revisions / get_revision (full route flow)
# ---------------------------------------------------------------------------

def test_create_revision_is_404_for_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.create_revision(
            submission_id=str(uuid.uuid4()),
            body=SubmissionRevisionCreate(content="x", source="manual_edit"),
            user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


def test_sequential_revisions_number_correctly_and_update_current_content():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    user = _User()

    r1 = asyncio.run(submissions_routes.create_revision(
        submission_id=str(sub.id),
        body=SubmissionRevisionCreate(content="v1 text", source="manual_edit"),
        user=user, db=db,
    ))
    assert r1["revision_number"] == 1
    assert r1["source"] == "manual_edit"
    assert sub.current_content == "v1 text"

    violation_id = uuid.uuid4()
    r2 = asyncio.run(submissions_routes.create_revision(
        submission_id=str(sub.id),
        body=SubmissionRevisionCreate(content="v2 text", source="apply_fix", applied_violation_ids=[violation_id]),
        user=user, db=db,
    ))
    assert r2["revision_number"] == 2
    assert r2["applied_violation_ids"] == [str(violation_id)]
    assert sub.current_content == "v2 text"

    # restore = re-POST an old revision's content with source='restore' — no
    # separate endpoint, just the same primitive with a new revision_number.
    r3 = asyncio.run(submissions_routes.create_revision(
        submission_id=str(sub.id),
        body=SubmissionRevisionCreate(content="v1 text", source="restore", note="restored to v1"),
        user=user, db=db,
    ))
    assert r3["revision_number"] == 3
    assert r3["source"] == "restore"
    assert r3["note"] == "restored to v1"
    assert sub.current_content == "v1 text"

    listed = asyncio.run(submissions_routes.list_revisions(submission_id=str(sub.id), user=user, db=db))
    assert [r["revision_number"] for r in listed["revisions"]] == [1, 2, 3]

    fetched = asyncio.run(submissions_routes.get_revision(submission_id=str(sub.id), revision_number=2, user=user, db=db))
    assert fetched["content"] == "v2 text"
    assert fetched["source"] == "apply_fix"


def test_get_revision_404_for_unknown_number():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.get_revision(submission_id=str(sub.id), revision_number=99, user=_User(), db=db))
    assert exc.value.status_code == 404


def test_second_submissions_revisions_start_at_one_not_four():
    db = FakeSession()
    sub_a, sub_b = _submission(), _submission()
    db.add(sub_a)
    db.add(sub_b)
    user = _User()
    for _ in range(3):
        asyncio.run(submissions_routes.create_revision(
            submission_id=str(sub_a.id),
            body=SubmissionRevisionCreate(content="x", source="manual_edit"),
            user=user, db=db,
        ))
    r_b = asyncio.run(submissions_routes.create_revision(
        submission_id=str(sub_b.id),
        body=SubmissionRevisionCreate(content="y", source="manual_edit"),
        user=user, db=db,
    ))
    assert r_b["revision_number"] == 1


# ---------------------------------------------------------------------------
# Comments CRUD
# ---------------------------------------------------------------------------

def test_create_comment_is_404_for_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.create_comment(
            submission_id=str(uuid.uuid4()),
            body=DocumentCommentCreate(body="note"),
            user=_User(), db=db,
        ))
    assert exc.value.status_code == 404


def test_create_comment_rejects_blank_body():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.create_comment(
            submission_id=str(sub.id),
            body=DocumentCommentCreate(body="   "),
            user=_User(), db=db,
        ))
    assert exc.value.status_code == 422


def test_comment_crud_round_trip():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    user = _User()

    created = asyncio.run(submissions_routes.create_comment(
        submission_id=str(sub.id),
        body=DocumentCommentCreate(anchor_text="guaranteed returns", page_number=2, body="needs a disclaimer"),
        user=user, db=db,
    ))
    assert created["anchor_text"] == "guaranteed returns"
    assert created["page_number"] == 2
    assert created["resolved"] is False

    second = asyncio.run(submissions_routes.create_comment(
        submission_id=str(sub.id),
        body=DocumentCommentCreate(body="second note"),
        user=user, db=db,
    ))

    listed = asyncio.run(submissions_routes.list_comments(submission_id=str(sub.id), user=user, db=db))
    assert [c["id"] for c in listed["comments"]] == [created["id"], second["id"]]

    updated = asyncio.run(submissions_routes.update_comment(
        submission_id=str(sub.id), comment_id=created["id"],
        body=DocumentCommentUpdate(resolved=True), user=user, db=db,
    ))
    assert updated["resolved"] is True
    assert updated["body"] == "needs a disclaimer"  # untouched — partial update

    updated_body = asyncio.run(submissions_routes.update_comment(
        submission_id=str(sub.id), comment_id=created["id"],
        body=DocumentCommentUpdate(body="revised note"), user=user, db=db,
    ))
    assert updated_body["body"] == "revised note"
    assert updated_body["resolved"] is True  # untouched by this partial update

    asyncio.run(submissions_routes.delete_comment(submission_id=str(sub.id), comment_id=created["id"], user=user, db=db))
    remaining = asyncio.run(submissions_routes.list_comments(submission_id=str(sub.id), user=user, db=db))
    assert [c["id"] for c in remaining["comments"]] == [second["id"]]

    # Deleting again (or an unknown id) is a no-op, not an error.
    result = asyncio.run(submissions_routes.delete_comment(submission_id=str(sub.id), comment_id=created["id"], user=user, db=db))
    assert result["id"] == created["id"]


def test_update_comment_404_for_unknown_id():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(submissions_routes.update_comment(
            submission_id=str(sub.id), comment_id=str(uuid.uuid4()),
            body=DocumentCommentUpdate(resolved=True), user=_User(), db=db,
        ))
    assert exc.value.status_code == 404
