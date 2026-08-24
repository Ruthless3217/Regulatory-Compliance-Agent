"""The /assignments routes.

Route functions are called directly with a FakeSession, the way the sibling
suites do — `Depends` never runs, so these cover handler behaviour and the
mapping of service refusals onto HTTP status codes, not the permission wiring
(that is test_role_hierarchy.py).
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import assignments as routes
from app.api.routes.assignments import AssignIn, CloseIn, ReassignIn, SendBackIn
from app.models.submission import Submission
from app.models.user import User
from tests.support.fake_session import FakeSession


class _User:
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role


@pytest.fixture
def db():
    return FakeSession()


def _submission(db):
    sub = Submission(id=uuid.uuid4(), title="Brochure", content_type="docx")
    db.add(sub)
    return sub


def _assign(db, sub, assignee, actor, **kw):
    return asyncio.run(routes.create_assignment(
        AssignIn(submission_id=str(sub.id), assignee_id=str(assignee.id), **kw),
        user=actor, db=db,
    ))


def test_create_assignment_returns_the_bucket_row(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)

    out = _assign(db, sub, reviewer, admin, priority="high", note="check disclaimers")

    assert out["status"] == "open"
    assert out["assignee_id"] == str(reviewer.id)
    assert out["priority"] == "high"
    assert db.commits == 1


def test_double_assign_is_a_409(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    _assign(db, sub, reviewer, admin)

    with pytest.raises(HTTPException) as exc:
        _assign(db, sub, reviewer, admin)
    assert exc.value.status_code == 409


def test_unknown_priority_is_a_400(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)

    with pytest.raises(HTTPException) as exc:
        _assign(db, sub, reviewer, admin, priority="catastrophic")
    assert exc.value.status_code == 400


def test_assigning_an_invisible_submission_is_a_404(db):
    """The guard runs before the service, so this route cannot become a way to
    confirm a document exists."""
    reviewer = _User("user")
    sub = _submission(db)

    with pytest.raises(HTTPException) as exc:
        _assign(db, sub, reviewer, reviewer)
    assert exc.value.status_code == 404


def test_start_by_a_non_assignee_is_a_403(db):
    admin, reviewer = _User("admin"), _User("user")
    created = _assign(db, _submission(db), reviewer, admin)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(created["id"], user=_User("user"), db=db))
    assert exc.value.status_code == 403


def test_illegal_transition_is_a_409(db):
    admin, reviewer = _User("admin"), _User("user")
    created = _assign(db, _submission(db), reviewer, admin)
    asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))
    assert exc.value.status_code == 409


def test_send_back_without_a_reason_is_a_400(db):
    admin, reviewer = _User("admin"), _User("user")
    created = _assign(db, _submission(db), reviewer, admin)
    asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))
    asyncio.run(routes.complete_assignment(created["id"], user=reviewer, db=db))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.send_back_assignment(
            created["id"], SendBackIn(reason="  "), user=admin, db=db))
    assert exc.value.status_code == 400


def test_reassign_returns_the_new_row(db):
    admin, first, second = _User("admin"), _User("user"), _User("user")
    created = _assign(db, _submission(db), first, admin)

    out = asyncio.run(routes.reassign_assignment(
        created["id"], ReassignIn(assignee_id=str(second.id)), user=admin, db=db))

    assert out["assignee_id"] == str(second.id)
    assert out["status"] == "open"
    assert out["id"] != created["id"]


def test_my_bucket_returns_only_my_active_assignments(db):
    admin, mine, theirs = _User("admin"), _User("user"), _User("user")
    for owner in (mine, theirs):
        _assign(db, _submission(db), owner, admin)

    out = asyncio.run(routes.my_bucket(user=mine, db=db))

    assert out["total"] == 1
    assert out["assignments"][0]["assignee_id"] == str(mine.id)


def test_my_bucket_excludes_closed_work(db):
    admin, reviewer = _User("admin"), _User("user")
    created = _assign(db, _submission(db), reviewer, admin)
    asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))
    asyncio.run(routes.complete_assignment(created["id"], user=reviewer, db=db))
    asyncio.run(routes.cancel_assignment(
        created["id"], routes.CancelIn(reason="withdrawn"), user=admin, db=db))

    assert asyncio.run(routes.my_bucket(user=reviewer, db=db))["total"] == 0


def test_unknown_assignment_is_a_404(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(str(uuid.uuid4()), user=_User("user"), db=db))
    assert exc.value.status_code == 404


def test_workload_counts_open_work_least_loaded_first(db):
    admin = _User("admin")
    busy = User(id=uuid.uuid4(), username="busy", role="user", is_active=True)
    idle = User(id=uuid.uuid4(), username="idle", role="user", is_active=True)
    db.add(busy)
    db.add(idle)
    for _ in range(2):
        _assign(db, _submission(db), busy, admin)

    rows = asyncio.run(routes.workload(user=admin, db=db))["reviewers"]

    assert [r["username"] for r in rows] == ["idle", "busy"]
    assert [r["open_count"] for r in rows] == [0, 2]


def test_assignment_for_submission_reports_active_and_history(db):
    admin, first, second = _User("admin"), _User("user"), _User("user")
    sub = _submission(db)
    created = _assign(db, sub, first, admin)
    asyncio.run(routes.reassign_assignment(
        created["id"], ReassignIn(assignee_id=str(second.id)), user=admin, db=db))

    out = asyncio.run(routes.assignment_for_submission(str(sub.id), user=admin, db=db))

    assert out["active"]["assignee_id"] == str(second.id)
    assert len(out["history"]) == 2


# ---------------------------------------------------------------------------
# Sign-off — the exit the lifecycle was missing
# ---------------------------------------------------------------------------

def _close(db, assignment_id, actor, outcome="approved", note=None):
    return asyncio.run(routes.close_assignment(
        assignment_id, CloseIn(outcome=outcome, note=note), user=actor, db=db,
    ))


def test_close_signs_off_and_returns_the_closed_row(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    a = _assign(db, sub, reviewer, admin)
    asyncio.run(routes.start_assignment(a["id"], user=reviewer, db=db))
    asyncio.run(routes.complete_assignment(a["id"], user=reviewer, db=db))

    out = _close(db, a["id"], admin, note="looks good")

    assert out["status"] == "closed"
    assert out["outcome"] == "approved"
    assert out["closed_at"] is not None


def test_close_frees_the_submission_for_a_new_assignment(db):
    """The point of closing: the partial unique index stops seeing an active
    row, so the document can be assigned again."""
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    a = _assign(db, sub, reviewer, admin)
    _close(db, a["id"], admin)

    again = _assign(db, sub, _User("user"), admin)
    assert again["status"] == "open"


def test_close_with_an_unknown_outcome_is_a_400(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    a = _assign(db, sub, reviewer, admin)

    with pytest.raises(HTTPException) as exc:
        _close(db, a["id"], admin, outcome="blessed")
    assert exc.value.status_code == 400


def test_closing_an_already_closed_assignment_is_a_409(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    a = _assign(db, sub, reviewer, admin)
    _close(db, a["id"], admin)

    with pytest.raises(HTTPException) as exc:
        _close(db, a["id"], admin)
    assert exc.value.status_code == 409


def test_close_of_a_missing_assignment_is_a_404(db):
    with pytest.raises(HTTPException) as exc:
        _close(db, str(uuid.uuid4()), _User("admin"))
    assert exc.value.status_code == 404
