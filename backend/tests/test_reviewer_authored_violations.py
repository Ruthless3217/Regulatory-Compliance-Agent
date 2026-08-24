"""POST /compliance/submissions/{id}/violations and
DELETE /compliance/violations/{id} — reviewer-authored findings (0031).

A reviewer can flag ANY span, including text no model run surfaced, and the row
is marked source='reviewer' so model-precision math can exclude it. The delete
side is the safety-critical half: a model-authored violation must NEVER be
removable through this route (it is the evidence the model's own precision is
measured against), and a reviewer-authored one only by its author or an admin.

Uses the in-memory fake Session the sibling suites use (Postgres-only UUID/JSONB
column types don't survive sqlite) and calls the route functions directly.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.sql.elements import Null

from app.api.routes import compliance as compliance_routes
from app.api.routes.compliance import ReviewerViolationCreate
from app.models.analysis_run import AnalysisRun
from app.models.compliance_check import ComplianceCheck
from app.models.submission import Submission
from app.models.violation import Violation


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Both routes fire-and-forget an audit event. audit.record opens a REAL
    SessionLocal, so without this every create test waits on a Postgres connect
    timeout inside asyncio.run's task cleanup."""
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.services.observability.audit.record", _noop)


class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._predicates = []
        self._order_key = None
        self._order_desc = False

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    def order_by(self, col):
        # `Column.desc()` wraps the column in a UnaryExpression — unwrap it and
        # remember the direction, since both new queries order descending.
        self._order_key = getattr(col, "element", col).key
        self._order_desc = hasattr(col, "element")
        return self

    def _matches(self, obj):
        # Emulate Postgres' UUID<->str coercion (path params arrive as str).
        return all(str(getattr(obj, k, None)) == str(v) for k, v in self._predicates)

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            rows.sort(key=lambda o: getattr(o, self._order_key, None) or 0, reverse=self._order_desc)
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()


class FakeSession:
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
    def __init__(self, role="user"):
        self.id = uuid.uuid4()
        self.role = role


def _submission(db, owner=None) -> Submission:
    """`owner` makes the document visible to that reviewer.

    Bucket-scoped visibility means a plain `user` can only open a submission
    they uploaded or that is assigned to them. These tests are about who may
    AUTHOR a finding, so the reviewer is given the document rather than
    promoted to admin — otherwise the role under test would no longer be the
    role the route sees.
    """
    s = Submission(
        id=uuid.uuid4(), title="t", content_type="text", original_content="orig",
        submitted_by=getattr(owner, "id", None),
    )
    db.add(s)
    return s


def _check(db, submission, checked_at=1) -> ComplianceCheck:
    c = ComplianceCheck(id=uuid.uuid4(), submission_id=submission.id, checked_at=checked_at)
    db.add(c)
    return c


def _payload(**overrides) -> ReviewerViolationCreate:
    body = {
        "current_text": "guaranteed 12% returns",
        "description": "Promises a guaranteed return",
        "severity": "high",
        "category": "irdai",
    }
    body.update(overrides)
    return ReviewerViolationCreate(**body)


def _create(db, submission_id, user, **overrides):
    return asyncio.run(compliance_routes.create_reviewer_violation(
        submission_id=str(submission_id), payload=_payload(**overrides), user=user, db=db,
    ))


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_is_404_for_missing_submission():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        _create(db, uuid.uuid4(), _User())
    assert exc.value.status_code == 404


def test_create_is_400_when_submission_was_never_analysed():
    """compliance_check_id is NOT NULL and inventing a check would fabricate a
    graded record — so this fails loudly instead of silently."""
    db = FakeSession()
    author = _User()
    sub = _submission(db, owner=author)
    with pytest.raises(HTTPException) as exc:
        _create(db, sub.id, author)
    assert exc.value.status_code == 400
    assert "not been analysed" in exc.value.detail
    assert db.rows_for(Violation) == []


def test_create_attaches_to_latest_check_and_its_run_and_marks_authorship():
    db = FakeSession()
    user = _User()
    sub = _submission(db, owner=user)
    _check(db, sub, checked_at=1)
    newest = _check(db, sub, checked_at=2)
    check_run = AnalysisRun(
        id=uuid.uuid4(),
        submission_id=sub.id,
        compliance_check_id=newest.id,
        run_number=2,
        status="completed",
    )
    db.add(check_run)
    # A later failed rerun has no check and must not be attached to a finding
    # that belongs to the last successful check.
    db.add(AnalysisRun(
        id=uuid.uuid4(),
        submission_id=sub.id,
        compliance_check_id=None,
        run_number=3,
        status="failed",
    ))

    out = _create(db, sub.id, user, suggested_fix="Remove the guarantee claim")

    assert out["source"] == "reviewer"
    assert out["created_by"] == str(user.id)
    assert out["rule_id"] is None          # no rule fired — a human wrote this
    assert out["confidence"] == 1.0        # not a fabricated model probability
    assert out["current_text"] == "guaranteed 12% returns"
    assert out["suggested_fix"] == "Remove the guarantee claim"

    row = db.rows_for(Violation)[0]
    assert row.compliance_check_id == newest.id
    assert row.analysis_run_id == check_run.id


def test_create_without_any_analysis_run_still_works():
    # A check can exist with no AnalysisRun row (pre-0022 data): the finding
    # still attaches, it just carries no run link.
    db = FakeSession()
    author = _User()
    sub = _submission(db, owner=author)
    _check(db, sub)
    _create(db, sub.id, author)
    assert db.rows_for(Violation)[0].analysis_run_id is None


def test_create_rejects_blank_and_unknown_field_values():
    with pytest.raises(ValidationError):
        _payload(current_text="")
    with pytest.raises(ValidationError):
        _payload(description="")
    with pytest.raises(ValidationError):
        _payload(severity="catastrophic")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def _delete(db, violation_id, user):
    return asyncio.run(compliance_routes.delete_reviewer_violation(
        violation_id=str(violation_id), user=user, db=db,
    ))


def _violation(db, source="reviewer", created_by=None) -> Violation:
    v = Violation(
        id=uuid.uuid4(), compliance_check_id=uuid.uuid4(), category="irdai",
        severity="high", description="d", source=source, created_by=created_by,
    )
    db.add(v)
    return v


def test_delete_is_404_for_unknown_violation():
    db = FakeSession()
    with pytest.raises(HTTPException) as exc:
        _delete(db, uuid.uuid4(), _User())
    assert exc.value.status_code == 404


def test_model_authored_violation_is_never_deletable():
    db = FakeSession()
    author = _User()
    v = _violation(db, source="model", created_by=author.id)
    for user in (author, _User(role="admin"), _User(role="super_admin")):
        with pytest.raises(HTTPException) as exc:
            _delete(db, v.id, user)
        assert exc.value.status_code == 403
    assert db.rows_for(Violation) == [v]


def test_violation_with_no_source_is_treated_as_model_authored():
    """Defensive: a row predating the migration (or a NULL slipping through)
    must fall on the safe side of the model/reviewer split."""
    db = FakeSession()
    v = _violation(db, source=None)
    with pytest.raises(HTTPException) as exc:
        _delete(db, v.id, _User(role="admin"))
    assert exc.value.status_code == 403


def test_author_can_delete_their_own_flag():
    db = FakeSession()
    author = _User()
    v = _violation(db, created_by=author.id)
    out = _delete(db, v.id, author)
    assert out["id"] == str(v.id)
    assert db.rows_for(Violation) == []


def test_another_reviewer_cannot_delete_someone_elses_flag():
    db = FakeSession()
    v = _violation(db, created_by=_User().id)
    with pytest.raises(HTTPException) as exc:
        _delete(db, v.id, _User())
    assert exc.value.status_code == 403
    assert db.rows_for(Violation) == [v]


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_feedback_review_holders_can_delete_any_reviewer_flag(role):
    # admin/super_admin hold feedback:review; a plain user does not.
    db = FakeSession()
    v = _violation(db, created_by=_User().id)
    _delete(db, v.id, _User(role=role))
    assert db.rows_for(Violation) == []
