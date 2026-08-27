"""Optimistic concurrency on the revision save path.

The bug this closes: `create_revision` accepted any write and mirrored it onto
`submissions.current_content` / `lexical_state` / `lexical_html` unconditionally,
so a reviewer editing a document someone else had already changed silently
replaced their work. The client autosaves two seconds after a document goes
dirty, so this needed no unusual timing — two tabs were enough.

The route's own comment in SubmissionWorkspaceContext had predicted it:
"last-write-wins on concurrent saves ... Fine because every UI writer awaits its
own call. Upgrade path: a single-slot save queue if background autosave ever
lands." Background autosave landed.

Two mechanisms, in this order:
  1. `expected_revision` — the client states the head it edited against, and a
     mismatch is refused before anything is written.
  2. UNIQUE(submission_id, revision_number) — the backstop for two writers who
     pass step 1 against the same head and race. Both surface as one 409.
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


# --- the schema carries the caller's expectation ---------------------------

def test_expected_revision_is_accepted_and_optional():
    with_it = SubmissionRevisionCreate(content="x", source="manual_edit", expected_revision=3)
    assert with_it.expected_revision == 3
    # Every pre-existing caller posts without it and must keep working.
    assert SubmissionRevisionCreate(content="x", source="manual_edit").expected_revision is None


# --- 1. the happy path -----------------------------------------------------

def test_first_save_against_an_unedited_document_expects_revision_zero():
    # 0 is a real claim, not a null: "I read a document nobody had edited".
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    out = _save(db, sub, user, content="edited", source="manual_edit", expected_revision=0)

    assert out["revision_number"] == 1
    assert sub.current_content == "edited"


def test_a_save_with_the_correct_expected_revision_succeeds():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    _save(db, sub, user, content="first", source="manual_edit", expected_revision=0)
    second = _save(db, sub, user, content="second", source="manual_edit", expected_revision=1)

    assert second["revision_number"] == 2
    assert sub.current_content == "second"


def test_omitting_expected_revision_still_works():
    # Deprecated, not rejected — a text-only caller that never read a revision
    # number would otherwise lose the ability to save at all.
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    out = _save(db, sub, user, content="edited", source="manual_edit")
    assert out["revision_number"] == 1


# --- 2. the stale write ----------------------------------------------------

def test_a_stale_save_is_refused_with_409():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    _save(db, sub, user, content="theirs", source="manual_edit", expected_revision=0)

    # Our reviewer still believes the document is at revision 0.
    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="mine", source="manual_edit", expected_revision=0)

    assert raised.value.status_code == 409
    detail = raised.value.detail
    assert detail["error"] == "revision_conflict"
    assert detail["expected_revision"] == 0
    assert detail["current_revision"] == 1
    assert detail["saved"] is False
    assert "not been saved" in detail["message"]


def test_a_stale_save_does_not_touch_the_canonical_document():
    # The whole point. The loser's text must not reach the submission.
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    _save(db, sub, user, content="theirs", source="manual_edit",
          expected_revision=0, lexical_state={"root": {}}, lexical_html="<p>theirs</p>")

    with pytest.raises(HTTPException):
        _save(db, sub, user, content="mine", source="manual_edit",
              expected_revision=0, lexical_state={"root": {"mine": True}}, lexical_html="<p>mine</p>")

    assert sub.current_content == "theirs"
    assert sub.lexical_html == "<p>theirs</p>"
    assert sub.lexical_state == {"root": {}}


def test_a_stale_save_creates_no_revision():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="theirs", source="manual_edit", expected_revision=0)

    with pytest.raises(HTTPException):
        _save(db, sub, user, content="mine", source="manual_edit", expected_revision=0)

    assert len(db.rows_for(SubmissionRevision)) == 1


def test_a_save_from_the_future_is_also_refused():
    # Not just "older than head" — anything that is not the head. A client
    # claiming revision 5 of a document at 1 has read something we did not write.
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="first", source="manual_edit", expected_revision=0)

    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="x", source="manual_edit", expected_revision=5)
    assert raised.value.status_code == 409


# --- 3. exactly one winner -------------------------------------------------

def test_of_two_writers_on_the_same_head_exactly_one_succeeds():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="base", source="manual_edit", expected_revision=0)

    # Both read head=1 and both try to write revision 2.
    winners, losers = 0, 0
    for content in ("writer-a", "writer-b"):
        try:
            _save(db, sub, user, content=content, source="manual_edit", expected_revision=1)
            winners += 1
        except HTTPException as e:
            assert e.status_code == 409
            losers += 1

    assert (winners, losers) == (1, 1)
    assert len(db.rows_for(SubmissionRevision)) == 2


def test_revision_numbers_stay_unique_and_contiguous():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)

    for i in range(5):
        _save(db, sub, user, content=f"v{i}", source="manual_edit", expected_revision=i)

    numbers = sorted(r.revision_number for r in db.rows_for(SubmissionRevision))
    assert numbers == [1, 2, 3, 4, 5]
    assert len(set(numbers)) == len(numbers)


# --- 4. the true race, decided by the UNIQUE constraint --------------------

class _RacingSession(FakeSession):
    """A session whose next commit loses a race, the way Postgres would.

    Two writers can both pass the `expected_revision` check when they read the
    same head in overlapping transactions; the database is what separates them.
    This reproduces that without a database: the constraint fires, and the route
    must convert it into the same 409 rather than a 500.

    `rollback` discards the rows added since the last commit, which is the part
    of transaction semantics this test needs. It does NOT undo attribute writes
    already made to `submission` in memory — a real session would, but modelling
    that here would be asserting the fidelity of the double rather than the
    behaviour of the route, so those assertions are deliberately left out.
    """

    def __init__(self):
        super().__init__()
        self.raise_on_next_commit = False
        self.rollbacks = 0
        self._pending: list = []

    def add(self, obj):
        if obj not in self._pending:
            self._pending.append(obj)
        super().add(obj)

    def commit(self):
        if self.raise_on_next_commit:
            self.raise_on_next_commit = False
            # Shaped like the real thing: psycopg2 names the violated
            # constraint, and the route now identifies the race by that name
            # rather than treating every integrity failure as a conflict. A
            # generic "unique violation" here would no longer be recognised —
            # correctly, since it is not what Postgres raises. See
            # test_revision_conflict_discrimination.py.
            raise IntegrityError(
                'duplicate key value violates unique constraint '
                '"uq_submission_revisions_submission_number"',
                None,
                Exception(
                    'duplicate key value violates unique constraint '
                    '"uq_submission_revisions_submission_number"'
                ),
            )
        self._pending.clear()
        super().commit()

    def rollback(self):
        self.rollbacks += 1
        for obj in self._pending:
            rows = self.rows_for(type(obj))
            if obj in rows:
                rows.remove(obj)
        self._pending.clear()


def test_losing_the_unique_race_returns_409_not_500():
    db, user = _RacingSession(), _User()
    sub = _submission()
    db.add(sub)
    db.commit()

    db.raise_on_next_commit = True
    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="mine", source="manual_edit", expected_revision=0)

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "revision_conflict"
    assert raised.value.detail["saved"] is False


def test_the_losing_writer_leaves_no_revision_behind():
    # Revision, current_content, working document, fix flags and the audit row
    # share one commit, so a conflict must roll all of them back together.
    db, user = _RacingSession(), _User()
    sub = _submission()
    db.add(sub)
    db.commit()

    db.raise_on_next_commit = True
    with pytest.raises(HTTPException):
        _save(db, sub, user, content="mine", source="manual_edit", expected_revision=0)

    assert db.rollbacks == 1
    assert db.rows_for(SubmissionRevision) == []


# --- 5. authorization is unchanged and still enforced ----------------------

def test_a_reviewer_who_cannot_see_the_submission_still_cannot_write_to_it():
    # visibility.py is the authority and this change must not weaken it: a
    # caller who may not see the document gets 404 before any concurrency
    # check runs, so the 409 path never leaks its existence.
    db, user = FakeSession(), _User(role="user")
    sub = _submission()          # not theirs, not assigned
    db.add(sub)

    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="mine", source="manual_edit", expected_revision=0)

    assert raised.value.status_code == 404
    assert len(db.rows_for(SubmissionRevision)) == 0


def test_an_admin_is_unaffected():
    db, user = FakeSession(), _User(role="admin")
    sub = _submission()
    db.add(sub)
    out = _save(db, sub, user, content="edited", source="manual_edit", expected_revision=0)
    assert out["revision_number"] == 1


def test_the_uploader_can_still_save_their_own_submission():
    db, user = FakeSession(), _User(role="user")
    sub = _submission(submitted_by=user.id)
    db.add(sub)
    out = _save(db, sub, user, content="edited", source="manual_edit", expected_revision=0)
    assert out["revision_number"] == 1


# --- 6. apply-fix uses the same door --------------------------------------

def test_apply_fix_obeys_the_same_concurrency_rule():
    # Apply-fix is not a separate mutation path: the client posts it through
    # this route with source="apply_fix", so it inherits the check for free.
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="base", source="manual_edit", expected_revision=0)

    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="fixed", source="apply_fix", expected_revision=0)
    assert raised.value.status_code == 409


def test_restore_obeys_the_same_concurrency_rule():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="base", source="manual_edit", expected_revision=0)

    with pytest.raises(HTTPException) as raised:
        _save(db, sub, user, content="old text", source="restore", expected_revision=0)
    assert raised.value.status_code == 409


# --- 7. the head helper ----------------------------------------------------

def test_current_revision_number_is_zero_before_any_edit():
    db = FakeSession()
    sub = _submission()
    db.add(sub)
    assert submissions_routes._current_revision_number(db, sub.id) == 0


def test_next_revision_number_still_means_head_plus_one():
    db, user = FakeSession(), _User()
    sub = _submission()
    db.add(sub)
    _save(db, sub, user, content="a", source="manual_edit", expected_revision=0)

    assert submissions_routes._current_revision_number(db, sub.id) == 1
    assert submissions_routes._next_revision_number(db, sub.id) == 2
