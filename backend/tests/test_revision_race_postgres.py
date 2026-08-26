"""The race, against a real PostgreSQL, with two real sessions.

Every other concurrency test here uses an in-memory session double. Those pin
the pre-check — a save whose `expected_revision` is not the head is refused —
but a double has no unique index, no row locks and no transactions, so it
cannot show what happens when two writers pass that check at the same instant.
`_RacingSession` in test_revision_optimistic_locking *simulates* the constraint
firing; it does not demonstrate that it fires.

A warning worth keeping, because the first version of this file fell into it:
simply doing "B reads 17, A commits 18, B saves" does NOT reach the constraint.
The route re-reads the head inside `create_revision`, and under READ COMMITTED
— which is what `app.database.SessionLocal` uses — that re-read sees 18 and the
*pre-check* refuses B. The test passed while proving nothing new.

To reach the constraint, A has to commit inside B's request: after B has read
the head and allocated its number, before B commits. `audit.record_sync` is the
last thing the route does before `db.commit()`, so hooking it gives an exact,
sleep-free interleave at that point. `_dup_calls` then asserts the refusal came
from the IntegrityError handler rather than the pre-check, so this test cannot
quietly go back to testing the cheap path.

Opt-in, because it needs a server:

    docker run -d --rm --name pg -e POSTGRES_PASSWORD=pw -p 5433:5432 postgres:15-alpine
    TEST_DATABASE_URL=postgresql://postgres:pw@localhost:5433/postgres \\
        pytest tests/test_revision_race_postgres.py

Without TEST_DATABASE_URL the module skips rather than pretending to pass. It
creates only the four tables this path touches, in a schema of its own that is
dropped afterwards, so it never migrates or touches a real database.
"""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.routes import submissions as submissions_routes
from app.models.audit_event import AuditEvent
from app.models.submission import Submission
from app.models.submission_revision import SubmissionRevision
from app.models.user import User
from app.schemas.submission import SubmissionRevisionCreate

DSN = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="needs a real PostgreSQL; set TEST_DATABASE_URL (see module docstring)",
)

SCHEMA = "revision_race_test"
TABLES = [User.__table__, Submission.__table__, SubmissionRevision.__table__, AuditEvent.__table__]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DSN, future=True)
    with eng.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    for t in TABLES:
        t.schema = SCHEMA
    try:
        User.metadata.create_all(eng, tables=TABLES)
        yield eng
    finally:
        for t in TABLES:
            t.schema = None
        with eng.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        eng.dispose()


@pytest.fixture
def session_factory(engine):
    # Mirrors app.database.SessionLocal, including autoflush=False — which is
    # what keeps the pending INSERT from being flushed before the commit inside
    # the route's try block, so the race surfaces where the handler catches it.
    return sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


@pytest.fixture
def seeded(session_factory):
    """A submission at revision 17, owned by a real user row."""
    db = session_factory()
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.test", role="admin")
    db.add(user)
    sub = Submission(
        id=uuid.uuid4(), title="Brochure", content_type="docx",
        original_content="original", current_content="revision 17 text",
        submitted_by=user.id,
    )
    db.add(sub)
    for n in range(1, 18):
        db.add(SubmissionRevision(
            id=uuid.uuid4(), submission_id=sub.id, revision_number=n,
            content=f"revision {n} text", source="manual_edit", created_by=user.id,
        ))
    db.commit()
    ids = (sub.id, user.id)
    db.close()
    yield ids


@pytest.fixture
def dup_calls(monkeypatch):
    """Counts entries into the IntegrityError discriminator, so a test can
    prove WHICH mechanism refused a write."""
    calls = []
    real = submissions_routes._is_duplicate_revision_number

    def counted(exc):
        result = real(exc)
        calls.append(result)
        return result

    monkeypatch.setattr(submissions_routes, "_is_duplicate_revision_number", counted)
    return calls


def _save(db, submission_id, user, **kwargs):
    return asyncio.run(
        submissions_routes.create_revision(
            submission_id=str(submission_id),
            body=SubmissionRevisionCreate(**kwargs),
            user=user, db=db,
        )
    )


def test_the_unique_constraint_decides_a_true_race(seeded, session_factory, dup_calls, monkeypatch):
    submission_id, user_id = seeded
    a, b = session_factory(), session_factory()
    try:
        user_b = b.get(User, user_id)

        # B opens its transaction and reads the head. Nothing is written yet.
        assert submissions_routes._current_revision_number(b, submission_id) == 17

        real_sync = submissions_routes.audit.record_sync
        interleaved = []

        def commit_a_first(session, *args, **kwargs):
            # Called once, from inside B's request: B has read head=17 and
            # allocated 18, and has not committed. A commits 18 right here.
            # This is the window the pre-check cannot close, and the only
            # reason the constraint has to exist.
            if session is b and not interleaved:
                interleaved.append(True)
                a.add(SubmissionRevision(
                    id=uuid.uuid4(), submission_id=submission_id, revision_number=18,
                    content="A's wording", source="manual_edit", created_by=user_id,
                ))
                a.get(Submission, submission_id).current_content = "A's wording"
                a.commit()
            return real_sync(session, *args, **kwargs)

        monkeypatch.setattr(submissions_routes.audit, "record_sync", commit_a_first)

        with pytest.raises(HTTPException) as raised:
            _save(b, submission_id, user_b, content="B's wording",
                  source="manual_edit", expected_revision=17)

        assert interleaved, "A never committed inside B's request; the race did not happen"
        # The load-bearing assertion: B was refused by the DATABASE, not by the
        # pre-check. Without this the test silently degrades into a duplicate
        # of the cheap path.
        assert dup_calls == [True], "the refusal did not come from the unique constraint"

        assert raised.value.status_code == 409
        assert raised.value.detail["error"] == "revision_conflict"
        assert raised.value.detail["saved"] is False
        assert raised.value.detail["expected_revision"] == 17
        assert raised.value.detail["current_revision"] == 18
    finally:
        a.close()
        b.close()

    # --- what survived, read through a third, clean session -----------------
    check = session_factory()
    try:
        rows = (
            check.query(SubmissionRevision)
            .filter(SubmissionRevision.submission_id == submission_id)
            .all()
        )
        assert len(rows) == 18, "B must not have created a nineteenth revision"
        eighteens = [r for r in rows if r.revision_number == 18]
        assert len(eighteens) == 1, "exactly one revision 18 may exist"
        assert eighteens[0].content == "A's wording"

        # The canonical document is A's. B's stale write never reached it.
        assert check.get(Submission, submission_id).current_content == "A's wording"

        # No partial B state: its audit row shared the rolled-back transaction.
        assert check.query(AuditEvent).filter(
            AuditEvent.scope_submission_id == submission_id
        ).count() == 0
    finally:
        check.close()


def test_the_loser_can_still_use_its_session_afterwards(seeded, session_factory):
    """The rollback must leave B usable, not poisoned.

    A handler that caught the IntegrityError without rolling back would leave
    the session in `InFailedSqlTransaction`, and every later statement on that
    connection would fail — starting with the one the handler itself makes to
    report the current revision.
    """
    submission_id, user_id = seeded
    a, b = session_factory(), session_factory()
    try:
        user_a, user_b = a.get(User, user_id), b.get(User, user_id)
        submissions_routes._current_revision_number(b, submission_id)  # B reads 17

        _save(a, submission_id, user_a, content="A", source="manual_edit", expected_revision=17)
        with pytest.raises(HTTPException):
            _save(b, submission_id, user_b, content="B", source="manual_edit", expected_revision=17)

        # B's session still works and now sees A's committed revision.
        assert submissions_routes._current_revision_number(b, submission_id) == 18

        # And B can retry against the new head — which is exactly what
        # "Keep my version" does in the client.
        retried = _save(b, submission_id, user_b, content="B's wording",
                        source="manual_edit", expected_revision=18)
        assert retried["revision_number"] == 19
    finally:
        a.close()
        b.close()


def test_the_pre_check_refuses_a_stale_save_before_any_write(seeded, session_factory, dup_calls):
    """The cheap path, against a real database: no row, no mutation, no audit
    event — the write never starts, and the constraint is never consulted."""
    submission_id, user_id = seeded
    db = session_factory()
    try:
        user = db.get(User, user_id)
        with pytest.raises(HTTPException) as raised:
            _save(db, submission_id, user, content="stale", source="manual_edit", expected_revision=3)
        assert raised.value.status_code == 409
        assert raised.value.detail["current_revision"] == 17
        assert dup_calls == [], "the pre-check should refuse this without reaching the database"
    finally:
        db.close()

    check = session_factory()
    try:
        assert check.query(SubmissionRevision).filter(
            SubmissionRevision.submission_id == submission_id
        ).count() == 17
        assert check.get(Submission, submission_id).current_content == "revision 17 text"
        assert check.query(AuditEvent).filter(
            AuditEvent.scope_submission_id == submission_id
        ).count() == 0
    finally:
        check.close()


def test_restore_is_refused_when_the_document_moved_on(seeded, session_factory):
    """P0-1's scenario end to end: a restore based on 17 must not overwrite 18.

    Restore is not a separate endpoint — it re-posts an old revision's content
    through this same route with source='restore' — so it inherits the check
    the moment the client sends `expected_revision`, which it now does.
    """
    submission_id, user_id = seeded
    a, b = session_factory(), session_factory()
    try:
        user_a, user_b = a.get(User, user_id), b.get(User, user_id)

        # Someone else advances the document to 18.
        _save(a, submission_id, user_a, content="newer wording",
              source="manual_edit", expected_revision=17)

        # Our reviewer's version-history panel was loaded when the head was 17
        # and asks to restore revision 3 on that basis.
        with pytest.raises(HTTPException) as raised:
            _save(b, submission_id, user_b, content="revision 3 text",
                  source="restore", note="Restored from revision 3", expected_revision=17)
        assert raised.value.status_code == 409
        assert raised.value.detail["current_revision"] == 18
    finally:
        a.close()
        b.close()

    check = session_factory()
    try:
        rows = check.query(SubmissionRevision).filter(
            SubmissionRevision.submission_id == submission_id
        ).all()
        assert len(rows) == 18, "the refused restore must not have written a revision"
        # 18 remains authoritative — the restore did not overwrite it.
        assert check.get(Submission, submission_id).current_content == "newer wording"
        assert not any(r.source == "restore" for r in rows)
    finally:
        check.close()
