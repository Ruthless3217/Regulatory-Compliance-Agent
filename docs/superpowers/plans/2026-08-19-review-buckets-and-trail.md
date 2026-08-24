# Review Buckets and Action Trail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin assign submissions to named reviewers so work lands in a per-reviewer bucket, and give admins a trustworthy trail of who did what to which document.

**Architecture:** One new table (`review_assignments`) wrapping a submission, one new column (`audit_events.scope_submission_id`), a role hierarchy expressed as set unions, a shared visibility guard applied to every submission-scoped route, and a read-only trail service that joins `audit_events` against `submission_revisions` for real diffs.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (imperative `Column` style), Alembic, PostgreSQL, pytest, Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md`

## Global Constraints

- **Never run `git commit` unless the user explicitly asks.** Commit steps below are written as the *content* of the commit to stage; ask before committing.
- **No test may call Azure OpenAI, Cohere, Groq, or any live API.** No network in tests.
- **No Postgres in tests.** Every existing suite uses an in-memory fake Session and calls route functions directly — Postgres-only `UUID`/`JSONB` column types do not survive sqlite. Follow that pattern; do not introduce a real database or testcontainers.
- **`backend/tests/` is gitignored.** Tests are local-only and will not appear in `git status`. Write them anyway.
- The Docker image bakes the code — a backend change needs an image rebuild, not just a restart.
- Migration numbering continues from `0037`. The new migration is `0038`, `down_revision = "0037"`.
- Models use the imperative `Column(...)` style with `from ..database import Base`. Match the surrounding files; do not introduce `Mapped[]` / `mapped_column`.
- Every new model must be added to both the import list and `__all__` in `backend/app/models/__init__.py`, or Alembic autogenerate and `test_imports.py` will miss it.

## Correction to the spec

Spec section 9 states the partial unique index is "asserted at the database level rather than through the service". That is not achievable with this repo's test infrastructure (no Postgres in tests). It is covered two ways instead:

1. **Application guard** — `assignment_service.assign()` refuses when an active assignment already exists, raising `ActiveAssignmentExists`. Unit-tested against the fake Session (Task 5).
2. **Index presence** — a test asserts migration `0038` declares `uq_review_assignments_active` with the correct `postgresql_where` clause (Task 3).

The database index remains the production backstop against a genuine concurrent race, which the application guard alone cannot win. Both are needed; only one is testable here.

---

## File Structure

**Backend — create**

| File | Responsibility |
|---|---|
| `backend/tests/support/__init__.py` | package marker |
| `backend/tests/support/fake_session.py` | the shared in-memory `FakeSession` / `FakeQuery`, extracted from the 11 copies that exist today |
| `backend/app/models/review_assignment.py` | the `ReviewAssignment` model and its status constants |
| `backend/alembic/versions/0038_review_assignments_and_trail.py` | table, indexes, partial unique index, `audit_events.scope_submission_id` |
| `backend/app/services/assignment_service.py` | lifecycle transitions, assign / reassign / start / complete / send-back / close / cancel |
| `backend/app/services/trail_service.py` | read-only `document_trail` and `reviewer_trail` |
| `backend/app/api/routes/assignments.py` | the `/assignments` router |
| `backend/app/auth/visibility.py` | `get_visible_submission`, `visible_submission_filter` |

**Backend — modify**

| File | Change |
|---|---|
| `app/auth/permissions.py` | role sets become unions; new permissions |
| `app/services/observability/audit.py` | add `record_sync` |
| `app/models/audit_event.py` | add `scope_submission_id` |
| `app/models/__init__.py` | register `ReviewAssignment` |
| `app/api/routes/submissions.py` | visibility guard on 15 routes; `/approve` permission; new audit emissions |
| `app/api/routes/compliance.py` | visibility guard on 8 routes |
| `app/api/routes/similar.py`, `admin_retrieval.py`, `admin_console.py` | visibility guard; drop dead D3 checks |
| `app/main.py` | mount the assignments router |

**Frontend — modify/create**: `app/(workspace)/layout.tsx`, `app/(super-admin)/super_admin/layout.tsx`, `components/workspace/Sidebar.tsx`, `lib/api.ts`, `lib/types.ts`, plus new `app/(workspace)/reviewers/` pages and assignment components.

---

## Task 1: Shared fake-session test helper

Eleven test files each define their own copy of `FakeSession`. This feature adds many route tests; extracting the helper first avoids writing a twelfth copy and is the enabler for every later task.

**Files:**
- Create: `backend/tests/support/__init__.py`
- Create: `backend/tests/support/fake_session.py`
- Test: `backend/tests/support/test_fake_session.py`

**Interfaces:**
- Consumes: nothing
- Produces: `FakeSession` (methods `query`, `add`, `commit`, `refresh`, `delete`, `get`, `rows_for`, attribute `commits`), `FakeQuery`. Imported by every later task as `from tests.support.fake_session import FakeSession`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/support/test_fake_session.py
"""The shared in-memory Session stand-in. Extracted from the copies in
test_submission_approval.py et al so new suites stop cloning it."""
import uuid

from app.models.submission import Submission
from tests.support.fake_session import FakeSession


def test_add_then_query_by_equality():
    db = FakeSession()
    sub = Submission(id=uuid.uuid4(), title="Brochure", content_type="docx")
    db.add(sub)

    found = db.query(Submission).filter(Submission.id == sub.id).first()

    assert found is sub


def test_query_returns_none_when_no_match():
    db = FakeSession()
    assert db.query(Submission).filter(Submission.id == uuid.uuid4()).first() is None


def test_commit_is_counted():
    db = FakeSession()
    db.commit()
    db.commit()
    assert db.commits == 2


def test_order_by_desc_sorts_descending():
    db = FakeSession()
    a = Submission(id=uuid.uuid4(), title="a", content_type="text", submitted_at=1)
    b = Submission(id=uuid.uuid4(), title="b", content_type="text", submitted_at=2)
    db.add(a)
    db.add(b)

    rows = db.query(Submission).order_by(Submission.submitted_at.desc()).all()

    assert [r.title for r in rows] == ["b", "a"]


def test_delete_removes_the_row():
    db = FakeSession()
    sub = Submission(id=uuid.uuid4(), title="gone", content_type="text")
    db.add(sub)
    db.delete(sub)
    assert db.query(Submission).filter(Submission.id == sub.id).first() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/support/test_fake_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.support'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tests/support/__init__.py
```

```python
# backend/tests/support/fake_session.py
"""In-memory stand-in for a SQLAlchemy Session.

The suite cannot use sqlite: the models lean on Postgres-only column types
(`UUID`, `JSONB`, `ARRAY`) that sqlite will not create. Route functions are
therefore called directly with this object in place of `db`.

Supports only what the routes actually do — equality filters, ordering,
first/all/count/scalar. Anything richer (``or_``, ``in_``) is deliberately
unsupported: a route needing it should be tested through its own pure
predicate instead of by growing this shim into a query engine.
"""
from sqlalchemy.sql.elements import Null


class FakeQuery:
    def __init__(self, session, target):
        # query(Model) or query(Model.column) — the column form is used by
        # export_common's staleness check.
        self._column = getattr(target, "key", None) if hasattr(target, "class_") else None
        self._model = target.class_ if self._column else target
        self._session = session
        self._predicates = []
        self._order_key = None
        self._order_desc = False
        self._limit = None

    def filter(self, *exprs):
        for e in exprs:
            val = None if isinstance(e.right, Null) else e.right.value
            self._predicates.append((e.left.key, val))
        return self

    # SQLAlchemy's keyword form, used by a few call sites.
    def filter_by(self, **kwargs):
        self._predicates.extend(kwargs.items())
        return self

    def order_by(self, col):
        # `Column.desc()` wraps the column in a UnaryExpression — unwrap it and
        # remember the direction.
        self._order_key = getattr(col, "element", col).key
        self._order_desc = hasattr(col, "element")
        return self

    def limit(self, n=None):
        self._limit = n
        return self

    def offset(self, _n=None):
        return self

    def _matches(self, obj):
        return all(str(getattr(obj, k, None)) == str(v) for k, v in self._predicates)

    def _rows(self):
        rows = [o for o in self._session.rows_for(self._model) if self._matches(o)]
        if self._order_key:
            vals = [getattr(o, self._order_key, None) for o in rows]
            if all(v is not None for v in vals):
                rows.sort(key=lambda o: getattr(o, self._order_key), reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()

    def count(self):
        return len(self._rows())

    def scalar(self):
        row = self.first()
        if row is None:
            return None
        return getattr(row, self._column) if self._column else row


class FakeSession:
    def __init__(self):
        self._store: dict = {}
        self.commits = 0

    def rows_for(self, model):
        return self._store.setdefault(model, [])

    def query(self, target):
        return FakeQuery(self, target)

    def add(self, obj):
        rows = self.rows_for(type(obj))
        if obj not in rows:
            rows.append(obj)

    def delete(self, obj):
        rows = self.rows_for(type(obj))
        if obj in rows:
            rows.remove(obj)

    def get(self, model, pk):
        return self.query(model).filter(model.id == pk).first()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def refresh(self, _obj):
        pass

    def flush(self):
        pass
```

Note on `get`: `FakeQuery.filter` reads `e.right.value`, so `model.id == pk`
must be a real SQLAlchemy expression — it is, because `model.id` is a Column.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/support/test_fake_session.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/tests/support/
# backend/tests is gitignored — expect "nothing added". That is correct;
# the helper is local-only like the rest of the suite.
```

---

## Task 2: Role hierarchy

Replaces three hand-maintained literal permission sets with unions, so `user ⊆ admin ⊆ super_admin` becomes structural. The current sets have already drifted: `super_admin` holds no submission permissions at all.

**Files:**
- Modify: `backend/app/auth/permissions.py` (whole file)
- Test: `backend/tests/test_role_hierarchy.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ROLE_PERMISSIONS: dict[str, frozenset[str]]`, `role_has(role: str, perm: str) -> bool` (unchanged signature). New permission strings `assignments:work`, `assignments:manage`, `trail:view`, `submission:approve`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_role_hierarchy.py
"""Role hierarchy (spec D5).

super_admin is a strict superset of admin, admin of user. `users:manage` is
super_admin's alone. These are asserted structurally so the sets cannot drift
apart again the way they already had — super_admin previously held no
submission permissions at all.
"""
import pytest

from app.auth.permissions import ROLE_PERMISSIONS, role_has


def test_hierarchy_is_a_strict_chain():
    user = ROLE_PERMISSIONS["user"]
    admin = ROLE_PERMISSIONS["admin"]
    super_admin = ROLE_PERMISSIONS["super_admin"]

    assert user < admin, "admin must be a strict superset of user"
    assert admin < super_admin, "super_admin must be a strict superset of admin"


def test_only_super_admin_manages_users():
    assert not role_has("user", "users:manage")
    assert not role_has("admin", "users:manage")
    assert role_has("super_admin", "users:manage")


def test_approval_is_admin_and_above():
    assert not role_has("user", "submission:approve")
    assert role_has("admin", "submission:approve")
    assert role_has("super_admin", "submission:approve")


def test_assignment_permissions():
    # everyone can work their own bucket; only admin+ can hand work out
    for r in ("user", "admin", "super_admin"):
        assert role_has(r, "assignments:work")
    assert not role_has("user", "assignments:manage")
    assert role_has("admin", "assignments:manage")
    assert role_has("super_admin", "assignments:manage")


def test_trail_is_admin_and_above():
    assert not role_has("user", "trail:view")
    assert role_has("admin", "trail:view")
    assert role_has("super_admin", "trail:view")


def test_console_stays_super_admin_only():
    # Deliberate: widening admin was not requested. admin answers
    # "who did what" through trail:view instead.
    for perm in ("console:view", "audit:view", "usage:view"):
        assert not role_has("admin", perm)
        assert role_has("super_admin", perm)


def test_super_admin_can_read_submissions():
    # regression: super_admin previously had no submission permissions
    for perm in ("submission:read", "submission:create", "analysis:run",
                 "comparison:use", "dashboard:view"):
        assert role_has("super_admin", perm)


def test_unknown_role_has_nothing():
    assert not role_has("nope", "submission:read")
    assert not role_has("", "submission:read")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_role_hierarchy.py -v`
Expected: FAIL — `test_hierarchy_is_a_strict_chain` fails (`admin` is not currently a subset of `super_admin`), and the four new-permission tests fail with the permissions absent.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/auth/permissions.py
"""Role → permission mapping.

Expressed as unions rather than three literal sets, so the hierarchy
user ⊆ admin ⊆ super_admin is a property of the code instead of something
three lists have to be kept in agreement about. They had already drifted:
super_admin previously held no submission permissions, which made the highest
role unable to open a document.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §4
"""

_USER = frozenset({
    "submission:create", "submission:read", "submission:delete", "analysis:run",
    "comparison:use", "dashboard:view", "knowledgebase:view",
    "rules:read", "feedback:submit",
    # Ability to act on an assignment. Does NOT grant access to any particular
    # one — every work action also checks assignee_id == caller.
    "assignments:work",
})

_ADMIN = _USER | frozenset({
    "rules:write", "rules:generate", "feedback:review",
    "assignments:manage",   # assign, reassign, cancel, see every bucket
    "trail:view",           # per-document and per-reviewer trail
    "submission:approve",   # split out of submission:create — reviewers lose it
})

# Console permissions stay here rather than in _ADMIN: widening admin was not
# asked for. The trail capability admin needs is carried by trail:view.
_SUPER_ADMIN = _ADMIN | frozenset({
    "users:manage",
    "console:view", "audit:view", "usage:view",
})

ROLE_PERMISSIONS = {
    "user": _USER,
    "admin": _ADMIN,
    "super_admin": _SUPER_ADMIN,
}


def role_has(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, frozenset())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_role_hierarchy.py -v`
Expected: PASS (8 passed)

Then run the existing suite for regressions, since permissions changed underneath it:
Run: `cd backend && python -m pytest tests/ -q`
Expected: no *new* failures. Record any pre-existing failures before this task so the comparison is honest.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/auth/permissions.py
```

---

## Task 3: ReviewAssignment model, migration 0038

**Files:**
- Create: `backend/app/models/review_assignment.py`
- Create: `backend/alembic/versions/0038_review_assignments_and_trail.py`
- Modify: `backend/app/models/audit_event.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/models/test_review_assignment.py`

**Interfaces:**
- Consumes: nothing
- Produces: `ReviewAssignment` (all columns per spec §1), `ACTIVE_STATUSES = ("open", "in_review", "awaiting_signoff")`, `AuditEvent.scope_submission_id`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/models/test_review_assignment.py
"""ReviewAssignment schema + migration 0038.

The partial unique index cannot be exercised here — the suite has no Postgres
(see the plan's "Correction to the spec"). What IS asserted is that the
migration declares it correctly; the behavioural guard lives in
assignment_service and is tested in test_assignment_service.py.
"""
import pathlib

from app.models.audit_event import AuditEvent
from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0038_review_assignments_and_trail.py"
)


def test_table_name_and_columns():
    cols = ReviewAssignment.__table__.columns.keys()
    for expected in (
        "id", "submission_id", "assignee_id", "assigned_by", "status", "priority",
        "due_at", "note", "assigned_at", "started_at", "completed_at",
        "closed_at", "closed_by", "outcome", "outcome_note", "superseded_by",
    ):
        assert expected in cols, f"missing column {expected}"
    assert ReviewAssignment.__tablename__ == "review_assignments"


def test_assignee_is_required_and_restricted():
    # A bucket with no owner is meaningless, so the column is NOT NULL and the
    # FK is RESTRICT rather than SET NULL.
    assignee = ReviewAssignment.__table__.columns["assignee_id"]
    assert assignee.nullable is False
    fk = list(assignee.foreign_keys)[0]
    assert fk.ondelete == "RESTRICT"


def test_submission_fk_cascades():
    fk = list(ReviewAssignment.__table__.columns["submission_id"].foreign_keys)[0]
    assert fk.ondelete == "CASCADE"


def test_active_statuses_are_the_three_open_states():
    assert ACTIVE_STATUSES == ("open", "in_review", "awaiting_signoff")


def test_audit_event_has_submission_scope():
    assert "scope_submission_id" in AuditEvent.__table__.columns.keys()


def test_migration_declares_the_partial_unique_index():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "uq_review_assignments_active" in src
    assert "unique=True" in src
    # The WHERE clause is the whole point — without it the index would forbid
    # a document ever being assigned twice, including sequentially.
    assert "postgresql_where" in src
    for status in ACTIVE_STATUSES:
        assert status in src


def test_migration_revision_chain():
    src = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0038"' in src
    assert 'down_revision: Union[str, None] = "0037"' in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/models/test_review_assignment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.review_assignment'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/models/review_assignment.py
"""A unit of review work: one submission handed to one reviewer.

Deliberately a separate table rather than an `assigned_to` column on
`submissions`, so a reassignment keeps the history of who held the document
before (`superseded_by` chains the rows) instead of overwriting it.

The reviewer-done state is `awaiting_signoff`, not `submitted` — in this
codebase "submitted" already means *uploaded* (`submissions.submitted_at`),
and overloading it would make queries ambiguous to read.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §1
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base

# The states in which a submission counts as "already assigned". The partial
# unique index in migration 0038 is defined over exactly this tuple.
ACTIVE_STATUSES = ("open", "in_review", "awaiting_signoff")


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT, not SET NULL: an assignment with no assignee is meaningless.
    # This system deactivates users (`is_active`) rather than deleting them,
    # so the constraint should never fire in normal operation.
    assignee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    status = Column(String(20), nullable=False, default="open", server_default="open")
    priority = Column(String(10), nullable=False, default="normal", server_default="normal")
    due_at = Column(DateTime(timezone=True))
    note = Column(Text)  # the admin's instruction to the reviewer

    assigned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    closed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    outcome = Column(String(20))       # approved | rejected | cancelled | superseded
    outcome_note = Column(Text)        # send-back / rejection reason
    superseded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("review_assignments.id", ondelete="SET NULL"),
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    submission = relationship("Submission")
    assignee = relationship("User", foreign_keys=[assignee_id])
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        # The bucket query: "everything open for this reviewer".
        Index("ix_review_assignments_assignee_status", "assignee_id", "status"),
    )
```

```python
# backend/alembic/versions/0038_review_assignments_and_trail.py
"""review_assignments + audit_events.scope_submission_id.

Adds the bucket (one submission handed to one reviewer) and the one column
that makes a document's trail a single indexed lookup.

`scope_submission_id` is a deliberate denormalization. A document's trail
includes events whose `target_id` is a violation or a comment, not the
submission — without this column, "everything that happened to this document"
degrades into a multi-step query or an unindexed JSONB scan of `metadata`.

The partial unique index is the load-bearing constraint: two admins assigning
the same document concurrently is a real race that a check-then-insert in
application code loses. The WHERE clause is essential — without it a document
could never be assigned twice even sequentially.

Additive, no backfill.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIVE = ("open", "in_review", "awaiting_signoff")
_ACTIVE_SQL = ", ".join(f"'{s}'" for s in ACTIVE)


def upgrade() -> None:
    op.create_table(
        "review_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(length=10), nullable=False, server_default="normal"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by"], ["review_assignments.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_review_assignments_submission_id", "review_assignments", ["submission_id"])
    op.create_index(
        "ix_review_assignments_assignee_status", "review_assignments",
        ["assignee_id", "status"],
    )
    op.create_index(
        "ix_review_assignments_due_active", "review_assignments", ["due_at"],
        postgresql_where=sa.text(f"status IN ({_ACTIVE_SQL})"),
    )
    # At most one ACTIVE assignment per submission, enforced by the database
    # so a concurrent double-assign is impossible rather than merely unlikely.
    op.create_index(
        "uq_review_assignments_active", "review_assignments", ["submission_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_ACTIVE_SQL})"),
    )

    op.add_column(
        "audit_events",
        sa.Column("scope_submission_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_events_scope_submission_id", "audit_events", "submissions",
        ["scope_submission_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_scope_submission_created", "audit_events",
        ["scope_submission_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_scope_submission_created", table_name="audit_events")
    op.drop_constraint("fk_audit_events_scope_submission_id", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "scope_submission_id")

    op.drop_index("uq_review_assignments_active", table_name="review_assignments")
    op.drop_index("ix_review_assignments_due_active", table_name="review_assignments")
    op.drop_index("ix_review_assignments_assignee_status", table_name="review_assignments")
    op.drop_index("ix_review_assignments_submission_id", table_name="review_assignments")
    op.drop_table("review_assignments")
```

In `backend/app/models/audit_event.py`, add the column after `session_id`:

```python
    # Denormalized document scope. Events whose target is a violation or a
    # comment still belong to a submission's trail; this makes that trail one
    # indexed lookup instead of a join chain or a JSONB scan (migration 0038).
    scope_submission_id = Column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True
    )
```

and add to `__table_args__`:

```python
        Index("ix_audit_scope_submission_created", "scope_submission_id", desc("created_at")),
```

In `backend/app/models/__init__.py`, add `from .review_assignment import ReviewAssignment` and `"ReviewAssignment"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/models/test_review_assignment.py tests/test_imports.py -v`
Expected: PASS

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/models/review_assignment.py \
        backend/app/models/audit_event.py \
        backend/app/models/__init__.py \
        backend/alembic/versions/0038_review_assignments_and_trail.py
```

Do **not** run `alembic upgrade head` yet — see Task 17 for the deploy sequence.

---

## Task 4: `audit.record_sync`

`audit.record()` opens its own session, commits separately, and swallows every exception. A mutation can succeed while its audit row silently vanishes, so the trail lies by omission. `record_sync` writes into the caller's session so the event and the change commit or roll back together.

**Files:**
- Modify: `backend/app/services/observability/audit.py`
- Test: `backend/tests/services/test_audit_record_sync.py`

**Interfaces:**
- Consumes: `FakeSession` from Task 1; `AuditEvent.scope_submission_id` from Task 3
- Produces: `audit.record_sync(db, event_type, *, actor=None, request=None, target_type=None, target_id=None, before=None, after=None, metadata=None, scope_submission_id=None) -> AuditEvent`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_audit_record_sync.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_audit_record_sync.py -v`
Expected: FAIL — `AttributeError: module 'app.services.observability.audit' has no attribute 'record_sync'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/services/observability/audit.py`:

```python
def record_sync(db, event_type, *, actor=None, request=None, target_type=None,
                target_id=None, before=None, after=None, metadata=None,
                scope_submission_id=None):
    """Write an audit event into the CALLER's session, without committing.

    The async `record` above is best-effort: its own session, its own commit,
    every exception swallowed. For telemetry that is the right trade. For the
    reviewer trail it is not — a mutation could commit while its audit row
    silently vanished, and an absent row is indistinguishable from an action
    that never happened.

    This adds the event to the caller's session so the caller's existing commit
    carries it: the event and the change it describes commit together or not at
    all. Exceptions are deliberately NOT caught — a failure here should roll the
    caller back rather than produce an unrecorded change.
    """
    from app.models.audit_event import AuditEvent

    ip = (
        extract_client_key(request, trust_forwarded_for=settings.trust_forwarded_for)
        if request else None
    )
    session_id = getattr(getattr(request, "state", None), "session_id", None)

    event = AuditEvent(
        event_type=event_type,
        actor_user_id=str(actor.id) if actor is not None else None,
        actor_role=getattr(actor, "role", None),
        actor_ip=ip,
        session_id=session_id,
        target_type=target_type,
        target_id=target_id,
        scope_submission_id=scope_submission_id,
        before_state=_json_safe(before),
        after_state=_json_safe(after),
        metadata_=_json_safe(metadata),
    )
    db.add(event)
    return event
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_audit_record_sync.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/services/observability/audit.py
```

---

## Task 5: Assignment service

**Files:**
- Create: `backend/app/services/assignment_service.py`
- Test: `backend/tests/services/test_assignment_service.py`

**Interfaces:**
- Consumes: `ReviewAssignment`, `ACTIVE_STATUSES` (Task 3); `audit.record_sync` (Task 4); `FakeSession` (Task 1)
- Produces:
  - `ALLOWED_TRANSITIONS: dict[str, set[str]]`
  - `class AssignmentError(Exception)`, `class ActiveAssignmentExists(AssignmentError)`, `class IllegalTransition(AssignmentError)`, `class NotAssignee(AssignmentError)`
  - `active_for_submission(db, submission_id) -> ReviewAssignment | None`
  - `assign(db, *, submission_id, assignee_id, actor, priority="normal", due_at=None, note=None) -> ReviewAssignment`
  - `reassign(db, *, assignment, new_assignee_id, actor, note=None) -> ReviewAssignment`
  - `start(db, *, assignment, actor) -> ReviewAssignment`
  - `complete(db, *, assignment, actor) -> ReviewAssignment`
  - `send_back(db, *, assignment, actor, reason) -> ReviewAssignment`
  - `close(db, *, assignment, actor, outcome, note=None) -> ReviewAssignment`
  - `cancel(db, *, assignment, actor, reason=None) -> ReviewAssignment`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_assignment_service.py
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
from app.models.review_assignment import ReviewAssignment
from app.services import assignment_service as svc
from tests.support.fake_session import FakeSession

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


class _User:
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role


@pytest.fixture
def db():
    return FakeSession()


@pytest.fixture
def admin():
    return _User("admin")


@pytest.fixture
def reviewer():
    return _User("user")


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
        svc.assign(db, submission_id=sub_id, assignee_id=_User("user").id, actor=admin)


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
        svc.start(db, assignment=a, actor=_User("user"))


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
    other = _User("user")
    first = svc.assign(db, submission_id=sub_id, assignee_id=reviewer.id, actor=admin)

    second = svc.reassign(db, assignment=first, new_assignee_id=other.id, actor=admin)

    assert first.status == "superseded"
    assert first.outcome == "superseded"
    assert first.superseded_by == second.id
    assert second.status == "open"
    assert second.assignee_id == other.id
    assert second.submission_id == sub_id


def test_reassign_carries_over_due_and_priority(db, admin, reviewer):
    first = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id,
                       actor=admin, priority="urgent", due_at=NOW)

    second = svc.reassign(db, assignment=first, new_assignee_id=_User("user").id, actor=admin)

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


def test_cancel_is_available_from_every_active_state(db, admin, reviewer):
    for setup in ("open", "in_review", "awaiting_signoff"):
        a = svc.assign(db, submission_id=uuid.uuid4(), assignee_id=reviewer.id, actor=admin)
        if setup in ("in_review", "awaiting_signoff"):
            svc.start(db, assignment=a, actor=reviewer)
        if setup == "awaiting_signoff":
            svc.complete(db, assignment=a, actor=reviewer)

        svc.cancel(db, assignment=a, actor=admin, reason="withdrawn")
        assert a.status == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_assignment_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.assignment_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/assignment_service.py
"""Review-assignment lifecycle.

    open --> in_review --> awaiting_signoff --> closed
      |          ^               |
      |          '-- send back --'
      '--> superseded (reassignment) / cancelled

Every state change goes through `_transition`, which consults one explicit
edge map. The alternative — `if status ==` checks spread across routes — is how
a workflow ends up with states nobody can enumerate.

Uniqueness of the active assignment is enforced twice on purpose: here, so the
API returns a clean 409, and by the partial unique index in migration 0038, so
two concurrent admins cannot both win the check-then-insert.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §2
"""
from datetime import datetime, timezone

from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.services.observability import audit

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_review", "superseded", "cancelled"},
    "in_review": {"awaiting_signoff", "superseded", "cancelled"},
    "awaiting_signoff": {"in_review", "closed", "superseded", "cancelled"},
    "closed": set(),
    "superseded": set(),
    "cancelled": set(),
}

VALID_PRIORITIES = ("low", "normal", "high", "urgent")
VALID_OUTCOMES = ("approved", "rejected", "cancelled", "superseded")


class AssignmentError(Exception):
    """Base for lifecycle refusals. Routes map these to 4xx."""


class ActiveAssignmentExists(AssignmentError):
    """This submission already has an open/in_review/awaiting_signoff row."""


class IllegalTransition(AssignmentError):
    """The requested state change is not an edge in ALLOWED_TRANSITIONS."""


class NotAssignee(AssignmentError):
    """A work action was attempted by someone who does not hold the assignment."""


def _now():
    return datetime.now(timezone.utc)


def active_for_submission(db, submission_id):
    """The one active assignment for this submission, or None.

    Queried per-status rather than with IN(...) because the test double
    supports only equality filters — and because the status index makes each
    lookup trivial anyway.
    """
    for status in ACTIVE_STATUSES:
        row = (
            db.query(ReviewAssignment)
            .filter(
                ReviewAssignment.submission_id == submission_id,
                ReviewAssignment.status == status,
            )
            .first()
        )
        if row is not None:
            return row
    return None


def _require_assignee(assignment, actor):
    if str(getattr(actor, "id", None)) != str(assignment.assignee_id):
        raise NotAssignee("This assignment belongs to someone else.")


def _transition(assignment, to_status):
    allowed = ALLOWED_TRANSITIONS.get(assignment.status, set())
    if to_status not in allowed:
        raise IllegalTransition(
            f"Cannot move an assignment from {assignment.status!r} to {to_status!r}."
        )
    assignment.status = to_status


def assign(db, *, submission_id, assignee_id, actor, priority="normal",
           due_at=None, note=None):
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Unknown priority {priority!r}.")
    if active_for_submission(db, submission_id) is not None:
        raise ActiveAssignmentExists(
            "This document is already assigned. Reassign it instead."
        )

    assignment = ReviewAssignment(
        submission_id=submission_id,
        assignee_id=assignee_id,
        assigned_by=getattr(actor, "id", None),
        status="open",
        priority=priority,
        due_at=due_at,
        note=note,
        assigned_at=_now(),
    )
    db.add(assignment)

    audit.record_sync(
        db, "assignment_created", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=submission_id,
        after={"assignee_id": str(assignee_id), "status": "open",
               "priority": priority, "due_at": due_at.isoformat() if due_at else None},
        metadata={"note": note},
    )
    return assignment


def reassign(db, *, assignment, new_assignee_id, actor, note=None):
    """Close the current row and open a fresh one for the new reviewer.

    Order matters: the old row is superseded BEFORE the new one is added, so
    the partial unique index never observes two active rows for the submission.
    """
    previous_assignee = assignment.assignee_id
    _transition(assignment, "superseded")
    assignment.outcome = "superseded"
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)

    replacement = ReviewAssignment(
        submission_id=assignment.submission_id,
        assignee_id=new_assignee_id,
        assigned_by=getattr(actor, "id", None),
        status="open",
        priority=assignment.priority,
        due_at=assignment.due_at,
        note=note if note is not None else assignment.note,
        assigned_at=_now(),
    )
    db.add(replacement)
    assignment.superseded_by = replacement.id

    audit.record_sync(
        db, "assignment_reassigned", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        before={"assignee_id": str(previous_assignee), "status": "open"},
        after={"assignee_id": str(new_assignee_id),
               "assignment_id": str(replacement.id)},
    )
    return replacement


def start(db, *, assignment, actor):
    _require_assignee(assignment, actor)
    _transition(assignment, "in_review")
    assignment.started_at = _now()
    audit.record_sync(
        db, "assignment_started", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "in_review"},
    )
    return assignment


def complete(db, *, assignment, actor):
    _require_assignee(assignment, actor)
    _transition(assignment, "awaiting_signoff")
    assignment.completed_at = _now()
    audit.record_sync(
        db, "assignment_completed", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "awaiting_signoff"},
    )
    return assignment


def send_back(db, *, assignment, actor, reason):
    """Return work to the reviewer who did it. Ownership is unchanged — this is
    not a reassignment, so no new row."""
    if not (reason or "").strip():
        raise ValueError("A send-back needs a reason.")
    _transition(assignment, "in_review")
    assignment.completed_at = None
    assignment.outcome_note = reason.strip()
    audit.record_sync(
        db, "assignment_sent_back", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        before={"status": "awaiting_signoff"},
        after={"status": "in_review"},
        metadata={"reason": reason.strip()},
    )
    return assignment


def close(db, *, assignment, actor, outcome, note=None):
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Unknown outcome {outcome!r}.")
    _transition(assignment, "closed")
    assignment.outcome = outcome
    assignment.outcome_note = note
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)
    audit.record_sync(
        db, "assignment_closed", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "closed", "outcome": outcome},
        metadata={"note": note},
    )
    return assignment


def cancel(db, *, assignment, actor, reason=None):
    _transition(assignment, "cancelled")
    assignment.outcome = "cancelled"
    assignment.outcome_note = reason
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)
    audit.record_sync(
        db, "assignment_cancelled", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "cancelled"},
        metadata={"reason": reason},
    )
    return assignment
```

Note: `ReviewAssignment.id` is populated by the model's `default=uuid.uuid4`
only on flush in real SQLAlchemy. Because `reassign` reads `replacement.id`
immediately, set it explicitly in the constructor if the default has not
applied — verify in Step 4 and, if `superseded_by` comes back `None`, add
`id=uuid.uuid4()` to both `ReviewAssignment(...)` constructions.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_assignment_service.py -v`
Expected: PASS (15 passed). If `test_reassign_supersedes_the_old_row_and_chains_it` fails on `superseded_by is None`, apply the explicit-id note above and re-run.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/services/assignment_service.py
```

---

## Task 6: Visibility guard

The bucket is only real if it is enforced on every route that reaches a submission. Filtering the list endpoint alone would leave a reviewer able to paste any UUID.

**Files:**
- Create: `backend/app/auth/visibility.py`
- Test: `backend/tests/test_submission_visibility.py`

**Interfaces:**
- Consumes: `ReviewAssignment` (Task 3), `role_has` (Task 2), `FakeSession` (Task 1)
- Produces:
  - `may_see_submission(db, submission, user) -> bool`
  - `get_visible_submission(db, submission_id, user) -> Submission` (raises `HTTPException(404)`)
  - `visible_submission_filter(user)` → a SQLAlchemy filter clause or `None` for unrestricted

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_submission_visibility.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_submission_visibility.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.visibility'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/auth/visibility.py
"""Which submissions a caller may see.

    admin, super_admin -> everything
    everyone else      -> assigned to me, or uploaded by me

Two entry points on purpose. `get_visible_submission` resolves ONE row and uses
only equality filters, so it works against the suite's in-memory Session double
and reads the same as the `db.query(...).first()` line it replaces.
`visible_submission_filter` builds the set-based clause for list queries, which
needs `or_`/`in_` and is exercised against a real database only.

A caller who may not see a submission gets 404, never 403: a 403 confirms the
document exists, which is itself a disclosure here.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §3
"""
from fastapi import HTTPException
from sqlalchemy import or_, select

from app.auth.permissions import role_has
from app.models.review_assignment import ReviewAssignment
from app.models.submission import Submission

# Holding this permission means "sees every bucket", which is also exactly the
# set of roles that may see every submission.
_UNRESTRICTED = "assignments:manage"


def _is_unrestricted(user) -> bool:
    return role_has(getattr(user, "role", "") or "", _UNRESTRICTED)


def may_see_submission(db, submission, user) -> bool:
    if _is_unrestricted(user):
        return True

    uid = getattr(user, "id", None)
    if uid is None:
        return False

    if submission.submitted_by is not None and str(submission.submitted_by) == str(uid):
        return True

    # Any assignment, not just an active one: a reviewer's own history must not
    # disappear the moment an admin signs the document off.
    assignment = (
        db.query(ReviewAssignment)
        .filter(
            ReviewAssignment.submission_id == submission.id,
            ReviewAssignment.assignee_id == uid,
        )
        .first()
    )
    return assignment is not None


def get_visible_submission(db, submission_id, user):
    """Drop-in replacement for the `db.query(Submission)...first()` + 404 pair
    that every submission-scoped route currently opens with."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if submission is None or not may_see_submission(db, submission, user):
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


def visible_submission_filter(user):
    """A WHERE clause for list queries, or None when the caller is unrestricted."""
    if _is_unrestricted(user):
        return None
    uid = getattr(user, "id", None)
    return or_(
        Submission.submitted_by == uid,
        Submission.id.in_(
            select(ReviewAssignment.submission_id).where(
                ReviewAssignment.assignee_id == uid
            )
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_submission_visibility.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/auth/visibility.py
```

---

## Task 7: Apply the guard to every submission-scoped route

**Files:**
- Modify: `backend/app/api/routes/submissions.py` (15 routes)
- Modify: `backend/app/api/routes/compliance.py` (8 routes)
- Modify: `backend/app/api/routes/similar.py` (1), `admin_retrieval.py` (1), `admin_console.py` (1)
- Test: `backend/tests/test_visibility_route_coverage.py`

**Interfaces:**
- Consumes: `get_visible_submission`, `visible_submission_filter` (Task 6)
- Produces: no new symbols — behavioural change only

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_visibility_route_coverage.py
"""Static guard: every route that resolves a submission must go through
`get_visible_submission`.

This is a source-level assertion rather than 26 request tests, and that is the
point — a NEW route added later without the guard fails this suite instead of
shipping a hole. Route tests would only cover the routes someone remembered to
write a test for.
"""
import pathlib
import re

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"

# Files carrying at least one submission-scoped route.
GUARDED_FILES = ("submissions.py", "compliance.py", "similar.py", "admin_retrieval.py")

# The pattern the guard replaces. Any surviving instance is an unguarded lookup.
RAW_LOOKUP = re.compile(
    r"db\.query\(\s*Submission\s*\)\s*\.\s*filter\(\s*Submission\.id\s*==",
    re.MULTILINE,
)

# admin_console reads submissions for the console's run list, which is
# super_admin-only and deliberately unrestricted.
ALLOWED_RAW = {"admin_console.py"}


def test_no_route_file_resolves_a_submission_without_the_guard():
    offenders = []
    for name in GUARDED_FILES:
        src = (ROUTES / name).read_text(encoding="utf-8")
        for match in RAW_LOOKUP.finditer(src):
            line = src[: match.start()].count("\n") + 1
            offenders.append(f"{name}:{line}")
    assert offenders == [], (
        "Unguarded submission lookups found. Replace each with "
        "`get_visible_submission(db, submission_id, user)`:\n  "
        + "\n  ".join(offenders)
    )


def test_guarded_files_import_the_guard():
    for name in GUARDED_FILES:
        src = (ROUTES / name).read_text(encoding="utf-8")
        assert "get_visible_submission" in src, f"{name} does not use the visibility guard"


def test_list_submissions_applies_the_set_filter():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    assert "visible_submission_filter" in src, (
        "list_submissions must scope its query, or the bucket is cosmetic"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_visibility_route_coverage.py -v`
Expected: FAIL — offenders listed for all four files.

- [ ] **Step 3: Write minimal implementation**

In each of `submissions.py`, `compliance.py`, `similar.py`, `admin_retrieval.py`, add the import:

```python
from app.auth.visibility import get_visible_submission
```

Then replace every occurrence of this pair:

```python
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
```

with:

```python
    submission = get_visible_submission(db, submission_id, user)
```

Notes for specific call sites:

- Some routes name the dependency `user`, others `_user` or `actor`. Use whatever that route already binds; if a route has no user dependency, add `user: dict = Depends(require("submission:read"))`.
- `compliance.py` background helpers `_run_analysis(submission_id, user=None, ...)` and `_analyze_and_stream(...)` run *after* the request returned. They must NOT re-check visibility (there is no request context and `user` may be `None`); the guard belongs on the route that spawns them. Leave their internal lookups alone and add a comment saying why.
- `admin_retrieval.py` is gated on an admin permission already, but route-level gating and row-level scoping are different things — apply the guard anyway so the rule has no exceptions to remember.

In `submissions.py`, scope `list_submissions`:

```python
from app.auth.visibility import get_visible_submission, visible_submission_filter

# ... inside list_submissions, replacing the two queries:
    scope = visible_submission_filter(user)

    query = db.query(Submission)
    counter = db.query(Submission)
    if scope is not None:
        query = query.filter(scope)
        counter = counter.filter(scope)

    submissions = (
        query
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = counter.count()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_visibility_route_coverage.py -v`
Expected: PASS (3 passed)

Run the full suite — this task changes 26 routes:
Run: `cd backend && python -m pytest tests/ -q`
Expected: no new failures versus the baseline recorded in Task 2.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/api/routes/submissions.py backend/app/api/routes/compliance.py \
        backend/app/api/routes/similar.py backend/app/api/routes/admin_retrieval.py
```

---

## Task 8: Approval becomes admin-only; drop the dead D3 checks

**Files:**
- Modify: `backend/app/api/routes/submissions.py:816` (the `/approve` dependency)
- Modify: `backend/app/api/routes/admin_console.py` (remove three unreachable D3 branches, update the module docstring)
- Test: `backend/tests/test_approval_permission.py`

**Interfaces:**
- Consumes: `role_has` (Task 2)
- Produces: none

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_approval_permission.py
"""Sign-off is admin-and-above (spec D4, two-person integrity).

/approve was guarded by `submission:create`, which every role holds — so the
reviewer who edited a document could also sign it off. It now needs
`submission:approve`.

Also pins that the "D3" in-handler role checks in admin_console are gone: once
only super_admin holds users:manage they are unreachable, and unreachable
authorization code reads as if it still protects something.
"""
import pathlib
import re

from app.auth.permissions import role_has

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"


def test_reviewers_cannot_approve():
    assert not role_has("user", "submission:approve")


def test_admins_and_super_admins_can_approve():
    assert role_has("admin", "submission:approve")
    assert role_has("super_admin", "submission:approve")


def test_approve_route_requires_the_approve_permission():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    approve = src[src.index('@router.post("/{submission_id}/approve")'):]
    signature = approve[: approve.index("):")]
    assert 'require("submission:approve")' in signature, (
        "/approve must require submission:approve, not submission:create"
    )


def test_dead_d3_checks_are_removed():
    src = (ROUTES / "admin_console.py").read_text(encoding="utf-8")
    leftovers = re.findall(r'role", None\) == "admin"', src)
    assert leftovers == [], (
        "Admin no longer holds users:manage, so these branches cannot run. "
        "Unreachable authorization code is worse than none — it reads as a guard."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_approval_permission.py -v`
Expected: FAIL on `test_approve_route_requires_the_approve_permission` and `test_dead_d3_checks_are_removed`.

- [ ] **Step 3: Write minimal implementation**

In `submissions.py`, change the `/approve` dependency:

```python
    user: dict = Depends(require("submission:approve")),
```

In `admin_console.py`, delete these three blocks (they cannot execute once only
super_admin holds `users:manage`):

```python
    # in create_user — DELETE
    if getattr(actor, "role", None) == "admin" and role != "user":
        raise HTTPException(status_code=403, detail="Admins may only create user accounts.")

    # in update_user — DELETE
    if getattr(actor, "role", None) == "admin" and getattr(user, "role", None) != "user":
        raise HTTPException(status_code=403, detail="Admins may only manage user accounts.")

    # in update_user's role branch — DELETE
    if getattr(actor, "role", None) == "admin" and body.role != "user":
        raise HTTPException(status_code=403, detail="Admins may only assign the user role.")
```

Update the docstrings that referenced them. In `create_user`:

```python
    """Provision a new account with a temp password (must be changed on first
    login). super_admin only — `users:manage` is no longer held by admin
    (spec D5), which supersedes the previous Decision D3 under which admins
    could provision `user` accounts."""
```

And the module docstring line:

```python
- user management  → ``users:manage``   (super_admin only — see spec D5)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_approval_permission.py tests/test_submission_approval.py -v`
Expected: PASS. `test_submission_approval.py` calls the route function directly, bypassing `Depends`, so the permission change does not affect it.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/api/routes/submissions.py backend/app/api/routes/admin_console.py
```

---

## Task 9: Assignments router

**Files:**
- Create: `backend/app/api/routes/assignments.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_assignments_router.py`

**Interfaces:**
- Consumes: `assignment_service` (Task 5), `get_visible_submission` (Task 6)
- Produces: router mounted at `/assignments` with:
  - `POST /assignments` — body `AssignIn(submission_id, assignee_id, priority, due_at, note)`
  - `POST /assignments/{id}/reassign` — body `ReassignIn(assignee_id, note)`
  - `POST /assignments/{id}/start`
  - `POST /assignments/{id}/complete`
  - `POST /assignments/{id}/send-back` — body `SendBackIn(reason)`
  - `POST /assignments/{id}/cancel` — body `CancelIn(reason)`
  - `GET /assignments/my` — the reviewer's bucket
  - `GET /assignments` — admin view, `?status=&assignee_id=`
  - `GET /assignments/workload` — `[{user_id, username, open_count}]` for the picker

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_assignments_router.py
"""The /assignments routes.

Route functions are called directly with a FakeSession, the way the sibling
suites do — `Depends` never runs, so these cover handler behaviour and the
service errors' HTTP mapping, not the permission wiring (that is Task 2's
test).
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes import assignments as routes
from app.api.routes.assignments import AssignIn, ReassignIn, SendBackIn
from app.models.review_assignment import ReviewAssignment
from app.models.submission import Submission
from tests.support.fake_session import FakeSession


class _User:
    def __init__(self, role="admin"):
        self.id = uuid.uuid4()
        self.role = role
        self.username = f"{role}-{str(self.id)[:4]}"


@pytest.fixture
def db():
    return FakeSession()


def _submission(db):
    sub = Submission(id=uuid.uuid4(), title="Brochure", content_type="docx")
    db.add(sub)
    return sub


def test_create_assignment_returns_the_bucket_row(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)

    out = asyncio.run(routes.create_assignment(
        AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id),
                 priority="high", note="check disclaimers"),
        user=admin, db=db,
    ))

    assert out["status"] == "open"
    assert out["assignee_id"] == str(reviewer.id)
    assert out["priority"] == "high"
    assert db.commits == 1


def test_double_assign_is_a_409(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    body = AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id))
    asyncio.run(routes.create_assignment(body, user=admin, db=db))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.create_assignment(body, user=admin, db=db))
    assert exc.value.status_code == 409


def test_assigning_an_invisible_submission_is_a_404(db):
    """The guard runs before the service, so an admin-only leak cannot open
    through this route either."""
    reviewer = _User("user")
    sub = _submission(db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.create_assignment(
            AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id)),
            user=reviewer, db=db,
        ))
    assert exc.value.status_code == 404


def test_start_by_a_non_assignee_is_a_403(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    created = asyncio.run(routes.create_assignment(
        AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id)),
        user=admin, db=db,
    ))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(created["id"], user=_User("user"), db=db))
    assert exc.value.status_code == 403


def test_illegal_transition_is_a_409(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    created = asyncio.run(routes.create_assignment(
        AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id)),
        user=admin, db=db,
    ))
    asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))
    assert exc.value.status_code == 409


def test_send_back_without_a_reason_is_a_400(db):
    admin, reviewer = _User("admin"), _User("user")
    sub = _submission(db)
    created = asyncio.run(routes.create_assignment(
        AssignIn(submission_id=str(sub.id), assignee_id=str(reviewer.id)),
        user=admin, db=db,
    ))
    asyncio.run(routes.start_assignment(created["id"], user=reviewer, db=db))
    asyncio.run(routes.complete_assignment(created["id"], user=reviewer, db=db))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.send_back_assignment(
            created["id"], SendBackIn(reason="  "), user=admin, db=db))
    assert exc.value.status_code == 400


def test_my_bucket_returns_only_my_active_assignments(db):
    admin, mine, theirs = _User("admin"), _User("user"), _User("user")
    for owner in (mine, theirs):
        sub = _submission(db)
        asyncio.run(routes.create_assignment(
            AssignIn(submission_id=str(sub.id), assignee_id=str(owner.id)),
            user=admin, db=db,
        ))

    out = asyncio.run(routes.my_bucket(user=mine, db=db))

    assert len(out["assignments"]) == 1
    assert out["assignments"][0]["assignee_id"] == str(mine.id)


def test_unknown_assignment_is_a_404(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.start_assignment(str(uuid.uuid4()), user=_User("user"), db=db))
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_assignments_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.routes.assignments'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/api/routes/assignments.py
"""Review assignments — the admin's hand-off and the reviewer's bucket.

Service errors map to HTTP here and nowhere else, so the lifecycle rules stay
in one place and the routes stay thin:

    ActiveAssignmentExists -> 409   already assigned; reassign instead
    IllegalTransition      -> 409   not an edge in the state machine
    NotAssignee            -> 403   someone else's work
    ValueError             -> 400   bad input (missing reason, unknown priority)

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §2
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.visibility import get_visible_submission
from app.database import get_db
from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.models.submission import Submission
from app.models.user import User
from app.services import assignment_service as svc

router = APIRouter(prefix="/assignments", tags=["Assignments"])


class AssignIn(BaseModel):
    submission_id: str
    assignee_id: str
    priority: str = "normal"
    due_at: Optional[datetime] = None
    note: Optional[str] = None


class ReassignIn(BaseModel):
    assignee_id: str
    note: Optional[str] = None


class SendBackIn(BaseModel):
    reason: str


class CancelIn(BaseModel):
    reason: Optional[str] = None


def _dict(a: ReviewAssignment) -> dict:
    return {
        "id": str(a.id),
        "submission_id": str(a.submission_id),
        "assignee_id": str(a.assignee_id),
        "assigned_by": str(a.assigned_by) if a.assigned_by else None,
        "status": a.status,
        "priority": a.priority,
        "due_at": a.due_at.isoformat() if a.due_at else None,
        "note": a.note,
        "outcome": a.outcome,
        "outcome_note": a.outcome_note,
        "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "closed_at": a.closed_at.isoformat() if a.closed_at else None,
        "superseded_by": str(a.superseded_by) if a.superseded_by else None,
    }


def _load(db, assignment_id) -> ReviewAssignment:
    a = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return a


def _run(fn, db):
    """Map service refusals onto HTTP and commit on success."""
    try:
        result = fn()
    except svc.ActiveAssignmentExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except svc.IllegalTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    except svc.NotAssignee as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.post("", status_code=201)
async def create_assignment(
    body: AssignIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    # Visibility first: an admin-only route still must not become a way to
    # confirm a document exists.
    get_visible_submission(db, body.submission_id, user)
    a = _run(lambda: svc.assign(
        db, submission_id=body.submission_id, assignee_id=body.assignee_id,
        actor=user, priority=body.priority, due_at=body.due_at, note=body.note,
    ), db)
    return _dict(a)


@router.post("/{assignment_id}/reassign")
async def reassign_assignment(
    assignment_id: str,
    body: ReassignIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    replacement = _run(lambda: svc.reassign(
        db, assignment=a, new_assignee_id=body.assignee_id, actor=user, note=body.note,
    ), db)
    return _dict(replacement)


@router.post("/{assignment_id}/start")
async def start_assignment(
    assignment_id: str,
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(lambda: svc.start(db, assignment=a, actor=user), db))


@router.post("/{assignment_id}/complete")
async def complete_assignment(
    assignment_id: str,
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(lambda: svc.complete(db, assignment=a, actor=user), db))


@router.post("/{assignment_id}/send-back")
async def send_back_assignment(
    assignment_id: str,
    body: SendBackIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(
        lambda: svc.send_back(db, assignment=a, actor=user, reason=body.reason), db))


@router.post("/{assignment_id}/cancel")
async def cancel_assignment(
    assignment_id: str,
    body: CancelIn,
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    a = _load(db, assignment_id)
    return _dict(_run(
        lambda: svc.cancel(db, assignment=a, actor=user, reason=body.reason), db))


@router.get("/my")
async def my_bucket(
    user=Depends(require("assignments:work")),
    db: Session = Depends(get_db),
):
    """Everything currently on this reviewer's plate."""
    rows = []
    for status in ACTIVE_STATUSES:
        rows.extend(
            db.query(ReviewAssignment)
            .filter(
                ReviewAssignment.assignee_id == user.id,
                ReviewAssignment.status == status,
            )
            .all()
        )
    return {"assignments": [_dict(a) for a in rows], "total": len(rows)}


@router.get("")
async def list_assignments(
    status: Optional[str] = Query(default=None),
    assignee_id: Optional[str] = Query(default=None),
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    q = db.query(ReviewAssignment)
    if status:
        q = q.filter(ReviewAssignment.status == status)
    if assignee_id:
        q = q.filter(ReviewAssignment.assignee_id == assignee_id)
    rows = q.all()
    return {"assignments": [_dict(a) for a in rows], "total": len(rows)}


@router.get("/workload")
async def workload(
    user=Depends(require("assignments:manage")),
    db: Session = Depends(get_db),
):
    """Open counts per reviewer, for the assign picker.

    Not a separate screen: the number is only ever wanted at the moment of
    choosing an assignee.
    """
    users = db.query(User).all()
    out = []
    for u in users:
        if u.role not in ("user", "admin"):
            continue
        open_count = 0
        for status in ACTIVE_STATUSES:
            open_count += (
                db.query(ReviewAssignment)
                .filter(
                    ReviewAssignment.assignee_id == u.id,
                    ReviewAssignment.status == status,
                )
                .count()
            )
        out.append({
            "user_id": str(u.id),
            "username": u.username or u.display_name or u.email,
            "role": u.role,
            "is_active": bool(u.is_active),
            "open_count": open_count,
        })
    out.sort(key=lambda r: r["open_count"])
    return {"reviewers": out}


@router.get("/for-submission/{submission_id}")
async def assignment_for_submission(
    submission_id: str,
    user=Depends(require("submission:read")),
    db: Session = Depends(get_db),
):
    """The banner on the submission page: current holder plus prior hand-offs."""
    get_visible_submission(db, submission_id, user)
    rows = (
        db.query(ReviewAssignment)
        .filter(ReviewAssignment.submission_id == submission_id)
        .all()
    )
    active = svc.active_for_submission(db, submission_id)
    return {
        "active": _dict(active) if active else None,
        "history": [_dict(a) for a in rows],
    }
```

In `backend/app/main.py`, register the router next to the other includes:

```python
from app.api.routes import assignments as assignments_routes
app.include_router(assignments_routes.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_assignments_router.py -v`
Expected: PASS (8 passed)

Run: `cd backend && python -m pytest tests/test_imports.py -v`
Expected: PASS — confirms the router import in `main.py` resolves.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/api/routes/assignments.py backend/app/main.py
```

---

## Task 10: Missing audit emissions

Six mutation paths currently change data and record nothing. The trail cannot be trusted while they are silent.

**Files:**
- Modify: `backend/app/api/routes/submissions.py` (revisions POST, four comment routes, export)
- Modify: `backend/app/api/routes/compliance.py` (violation actions, reviewer-authored violations)
- Test: `backend/tests/test_audit_emission_coverage.py`

**Interfaces:**
- Consumes: `audit.record_sync` (Task 4)
- Produces: event types `submission_edited`, `comment_created`, `comment_updated`, `comment_deleted`, `export_generated`, `violation_dismissed`, `violation_restored`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_audit_emission_coverage.py
"""Mutations that must leave a trace.

Editing a document, commenting on it, dismissing a finding and exporting it all
changed state while recording nothing. For a compliance trail an unrecorded
change is the failure mode that matters: the trail reads as complete while
being wrong.
"""
import pathlib
import re

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"


def _handler_body(src: str, decorator: str) -> str:
    start = src.index(decorator)
    nxt = src.find("\n@router.", start + 1)
    return src[start: nxt if nxt != -1 else len(src)]


def test_document_edits_are_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/{submission_id}/revisions")')
    assert "submission_edited" in body
    # record_sync, not the fire-and-forget record: an edit whose audit row
    # vanished is an edit nobody can attribute.
    assert "record_sync" in body


def test_comment_routes_are_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    for decorator, event in (
        ('@router.post("/{submission_id}/comments")', "comment_created"),
        ('@router.patch("/{submission_id}/comments/{comment_id}")', "comment_updated"),
        ('@router.delete("/{submission_id}/comments/{comment_id}")', "comment_deleted"),
    ):
        assert event in _handler_body(src, decorator), f"{event} not emitted"


def test_export_is_recorded():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.get("/{submission_id}/export/{kind}")')
    assert "export_generated" in body


def test_violation_actions_carry_the_document_scope():
    """A violation event belongs to its document's trail, which is what
    scope_submission_id is for."""
    src = (ROUTES / "compliance.py").read_text(encoding="utf-8")
    body = _handler_body(src, '@router.post("/violations/{violation_id}/actions")')
    assert "scope_submission_id" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_audit_emission_coverage.py -v`
Expected: FAIL — all four tests.

- [ ] **Step 3: Write minimal implementation**

Add to the imports of both route modules:

```python
from app.services.observability import audit
```

In `submissions.py`, inside `POST /{submission_id}/revisions`, immediately before the existing `db.commit()`:

```python
    # record_sync rather than the async record: the event rides this
    # transaction, so an edit can never commit without its trail row.
    audit.record_sync(
        db, "submission_edited", actor=user,
        target_type="submission", target_id=str(submission.id),
        scope_submission_id=submission.id,
        metadata={
            "revision_number": revision.revision_number,
            "source": revision.source,
            "note": revision.note,
        },
    )
```

In `POST /{submission_id}/comments`, before its commit:

```python
    audit.record_sync(
        db, "comment_created", actor=user,
        target_type="comment", target_id=str(comment.id),
        scope_submission_id=submission.id,
        after={"body": comment.body, "anchor_text": comment.anchor_text},
    )
```

In `PATCH /{submission_id}/comments/{comment_id}`, capture `before` prior to mutating, then before the commit:

```python
    audit.record_sync(
        db, "comment_updated", actor=user,
        target_type="comment", target_id=str(comment.id),
        scope_submission_id=submission.id,
        before=previous, after={"body": comment.body, "resolved": comment.resolved},
    )
```

In `DELETE /{submission_id}/comments/{comment_id}`, before the commit:

```python
    audit.record_sync(
        db, "comment_deleted", actor=user,
        target_type="comment", target_id=str(comment_id),
        scope_submission_id=submission.id,
        before={"body": comment.body},
    )
```

In `GET /{submission_id}/export/{kind}` the handler does not otherwise write, so
use the async recorder and give it the scope — an export is disclosure, worth
recording, but not worth failing the download over:

```python
    asyncio.create_task(audit.record(
        "export_generated", actor=user,
        target_type="submission", target_id=str(submission.id),
        metadata={"kind": kind, "scope_submission_id": str(submission.id)},
    ))
```

Then extend `audit.record`'s signature in `app/services/observability/audit.py` to accept and store `scope_submission_id` the same way `record_sync` does, and use it here instead of the metadata fallback:

```python
async def record(event_type, *, actor=None, request=None, target_type=None, target_id=None,
                 before=None, after=None, metadata=None, scope_submission_id=None):
    ...
            event = AuditEvent(
                ...
                scope_submission_id=scope_submission_id,
                ...
            )
```

In `compliance.py`, in `POST /violations/{violation_id}/actions`, add the scope to the existing audit call:

```python
    asyncio.create_task(audit.record(
        "violation_action_submitted", actor=user,
        target_type="violation", target_id=violation_id,
        scope_submission_id=submission_id_for(violation),
        metadata=payload.model_dump(),
    ))
```

where `submission_id_for(violation)` resolves through `violation.compliance_check.submission_id` — the check is already loaded in that handler; reuse it rather than re-querying.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_audit_emission_coverage.py tests/test_submission_revisions_and_comments.py tests/test_reviewer_actions.py -v`
Expected: PASS. If the revision/comment suites fail because their fake `db` now receives an extra `AuditEvent` row, that is the tests counting rows too loosely — scope their assertions to the model they mean.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/api/routes/submissions.py backend/app/api/routes/compliance.py \
        backend/app/services/observability/audit.py
```

---

## Task 11: Trail service and routes

**Files:**
- Create: `backend/app/services/trail_service.py`
- Modify: `backend/app/api/routes/assignments.py` (add the two trail routes)
- Test: `backend/tests/services/test_trail_service.py`

**Interfaces:**
- Consumes: `AuditEvent.scope_submission_id` (Task 3), `SubmissionRevision`, `ReviewAssignment`
- Produces:
  - `document_trail(db, submission_id) -> list[dict]` — keys `id, at, event_type, actor_id, actor_role, target_type, target_id, metadata, diff`
  - `reviewer_trail(db, user_id, limit=100) -> dict` — keys `activity` (same row shape), `stats`
  - `GET /assignments/trail/document/{submission_id}`
  - `GET /assignments/trail/reviewer/{user_id}`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_trail_service.py
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
        scope_submission_id=sub_id, actor_user_id=str(actor_id) if actor_id else None,
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


def test_reviewer_trail_returns_only_that_actors_events(db):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    _event(db, uuid.uuid4(), "submission_edited", T0, actor_id=mine)
    _event(db, uuid.uuid4(), "submission_edited", T0, actor_id=theirs)

    out = trail_service.reviewer_trail(db, mine)

    assert len(out["activity"]) == 1


def test_reviewer_stats_count_assignments(db):
    reviewer = uuid.uuid4()
    for status in ("open", "in_review", "closed", "closed"):
        db.add(ReviewAssignment(id=uuid.uuid4(), submission_id=uuid.uuid4(),
                                assignee_id=reviewer, status=status))

    stats = trail_service.reviewer_trail(db, reviewer)["stats"]

    assert stats["open"] == 2      # open + in_review are both still on the plate
    assert stats["closed"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/services/test_trail_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.trail_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/trail_service.py
"""Read-only assembly of the reviewer trail.

Two sources, joined rather than duplicated into a third table:

* `audit_events` — what someone did, and when. Scoped per document by
  `scope_submission_id` (migration 0038), so one indexed query gets a whole
  document's history including events whose target is a violation or comment.
* `submission_revisions` — the document text at each edit. An edit event
  carries `metadata.revision_number`, which is enough to fetch revisions N and
  N-1 and show a real before/after.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md §7
"""
from app.models.audit_event import AuditEvent
from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.models.submission_revision import SubmissionRevision

EDIT_EVENTS = ("submission_edited",)


def _revision_content(db, submission_id, number):
    if number is None or number < 1:
        return ""
    row = (
        db.query(SubmissionRevision)
        .filter(
            SubmissionRevision.submission_id == submission_id,
            SubmissionRevision.revision_number == number,
        )
        .first()
    )
    return (row.content or "") if row is not None else ""


def _diff_for(db, submission_id, event):
    """before/after text for an edit event, or None when it is not an edit or
    the revision it names is gone.

    Events predating this feature carry no revision_number; they render without
    a diff rather than raising.
    """
    if event.event_type not in EDIT_EVENTS:
        return None
    meta = event.metadata_ or {}
    number = meta.get("revision_number")
    if number is None:
        return None
    return {
        "revision_number": number,
        "before": _revision_content(db, submission_id, number - 1),
        "after": _revision_content(db, submission_id, number),
    }


def _row(db, submission_id, event):
    return {
        "id": str(event.id),
        "at": event.created_at.isoformat() if event.created_at else None,
        "event_type": event.event_type,
        "actor_id": event.actor_user_id,
        "actor_role": event.actor_role,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "metadata": event.metadata_ or {},
        "diff": _diff_for(db, submission_id, event) if submission_id else None,
    }


def document_trail(db, submission_id):
    """Everything that happened to one document, newest first."""
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.scope_submission_id == submission_id)
        .all()
    )
    events.sort(key=lambda e: e.created_at, reverse=True)
    return [_row(db, submission_id, e) for e in events]


def reviewer_trail(db, user_id, limit: int = 100):
    """Everything one reviewer did, plus their workload counts."""
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.actor_user_id == str(user_id))
        .all()
    )
    events.sort(key=lambda e: e.created_at, reverse=True)
    activity = [_row(db, e.scope_submission_id, e) for e in events[:limit]]

    assignments = (
        db.query(ReviewAssignment)
        .filter(ReviewAssignment.assignee_id == user_id)
        .all()
    )
    open_count = sum(1 for a in assignments if a.status in ACTIVE_STATUSES)
    closed_count = sum(1 for a in assignments if a.status == "closed")

    return {
        "activity": activity,
        "stats": {
            "open": open_count,
            "closed": closed_count,
            "total": len(assignments),
        },
    }
```

Add to `backend/app/api/routes/assignments.py`:

```python
from app.services import trail_service


@router.get("/trail/document/{submission_id}")
async def document_trail_route(
    submission_id: str,
    user=Depends(require("trail:view")),
    db: Session = Depends(get_db),
):
    get_visible_submission(db, submission_id, user)
    return {"trail": trail_service.document_trail(db, submission_id)}


@router.get("/trail/reviewer/{user_id}")
async def reviewer_trail_route(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(require("trail:view")),
    db: Session = Depends(get_db),
):
    return trail_service.reviewer_trail(db, user_id, limit=limit)
```

Route-ordering note: `/trail/...` and `/my` and `/workload` are all literal
paths under the same prefix as `/{assignment_id}/...`. FastAPI matches in
declaration order, and none of these collide with `/{assignment_id}` because
that route always has a further segment — but keep the literal routes declared
before any bare `/{assignment_id}` route added later.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/services/test_trail_service.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add backend/app/services/trail_service.py backend/app/api/routes/assignments.py
```

---

## Task 12: Unblock the workspace for super_admin

**Files:**
- Modify: `frontend/app/(workspace)/layout.tsx:31`
- Modify: `frontend/app/(super-admin)/super_admin/layout.tsx`
- Modify: `frontend/components/workspace/Sidebar.tsx`

**Interfaces:**
- Consumes: `Me` type (existing, has `role`, `must_change_password`)
- Produces: none

- [ ] **Step 1: Write the failing check**

There is no frontend test runner in this repo, so this task is verified by
inspection and by running the app. Write the check as an assertion you will
confirm in Step 4, not as a test file.

Expected after the change:
1. A super_admin visiting `/` sees the workspace, not a redirect to `/super_admin`.
2. A super_admin with `must_change_password` visiting `/super_admin` is redirected to `/account/change-password`.
3. A super_admin sees a Console link in the workspace sidebar; an admin and a user do not.

- [ ] **Step 2: Confirm the current behaviour fails those expectations**

Run: `cd frontend && npm run dev`
Log in as a super_admin and visit `/`.
Expected now: bounced to `/super_admin` — expectation 1 fails.

- [ ] **Step 3: Write minimal implementation**

In `frontend/app/(workspace)/layout.tsx`, delete line 31:

```ts
  if (me.role === "super_admin") redirect("/super_admin");   // DELETE
```

Leave the `must_change_password` redirect above it in place.

In `frontend/app/(super-admin)/super_admin/layout.tsx`, add the same
password guard the workspace has — a super_admin could otherwise skip the
forced change by going straight to the console:

```ts
export default async function SuperAdminLayout({ children }: { children: React.ReactNode }) {
  const me = await getServerMe();
  if (!me) redirect("/login");
  // Matches the workspace layout. Without this a temp-password super_admin can
  // skip the forced change simply by landing on /super_admin.
  if (me.must_change_password) redirect("/account/change-password");
  if (me.role !== "super_admin") redirect("/");
  ...
```

Add a Workspace link to `SuperAdminSidebar`'s `nav` array so the two halves are
reachable from each other:

```tsx
    { label: "Workspace", href: "/", icon: <FileText className="w-4 h-4" /> },
```

(import `FileText` from `lucide-react` alongside the existing icons).

In `frontend/components/workspace/Sidebar.tsx`, add a Console section visible
only to super_admin. Extend the existing gate rather than adding a second
mechanism:

```tsx
const ADMIN_ROLES = new Set(["admin", "super_admin"]);
const CONSOLE_ROLES = new Set(["super_admin"]);
```

Add to `SECTIONS`:

```tsx
  {
    // Console permissions (console:view / audit:view / usage:view) are
    // super_admin's alone — admin answers "who did what" through the
    // Reviewers page instead. Mirrors backend/app/auth/permissions.py.
    title: "Console",
    items: [
      { label: "Admin console", href: "/super_admin", icon: <Shield className="h-3.5 w-3.5" /> },
    ],
  },
```

and extend the filter:

```tsx
  const sections = SECTIONS.filter(s => {
    if (s.title === "Admin") return ADMIN_ROLES.has(me?.role ?? "");
    if (s.title === "Console") return CONSOLE_ROLES.has(me?.role ?? "");
    return true;
  }).map(s => {
```

Import `Shield` from `lucide-react`.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors.

Run: `cd frontend && npm run dev`, then check each expectation from Step 1 by
logging in as super_admin, admin, and user in turn.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add "frontend/app/(workspace)/layout.tsx" \
        "frontend/app/(super-admin)/super_admin/layout.tsx" \
        frontend/components/workspace/Sidebar.tsx
```

---

## Task 13: Frontend types and API client

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`

**Interfaces:**
- Consumes: the routes from Tasks 9 and 11
- Produces: types `ReviewAssignment`, `AssignmentStatus`, `AssignmentPriority`, `WorkloadRow`, `TrailRow`, `TrailDiff`, `ReviewerTrail`; functions `listMyBucket`, `listAssignments`, `assignSubmission`, `reassignAssignment`, `startAssignment`, `completeAssignment`, `sendBackAssignment`, `cancelAssignment`, `assignmentWorkload`, `assignmentForSubmission`, `documentTrail`, `reviewerTrail`

- [ ] **Step 1: Write the failing check**

Verified by `tsc`. Expected after the change: `npx tsc --noEmit` passes and the
new functions are importable from `@/lib/api`.

- [ ] **Step 2: Confirm it fails**

Run: `cd frontend && npx tsc --noEmit`
Then add a scratch import of `assignSubmission` in a page — it will error with
"has no exported member". Remove the scratch import before Step 3.

- [ ] **Step 3: Write minimal implementation**

Append to `frontend/lib/types.ts`:

```ts
export type AssignmentStatus =
  | "open" | "in_review" | "awaiting_signoff"
  | "closed" | "superseded" | "cancelled";

export type AssignmentPriority = "low" | "normal" | "high" | "urgent";

export interface ReviewAssignment {
  id: string;
  submission_id: string;
  assignee_id: string;
  assigned_by: string | null;
  status: AssignmentStatus;
  priority: AssignmentPriority;
  due_at: string | null;
  note: string | null;
  outcome: string | null;
  outcome_note: string | null;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  closed_at: string | null;
  superseded_by: string | null;
}

export interface WorkloadRow {
  user_id: string;
  username: string;
  role: string;
  is_active: boolean;
  open_count: number;
}

export interface TrailDiff {
  revision_number: number;
  before: string;
  after: string;
}

export interface TrailRow {
  id: string;
  at: string | null;
  event_type: string;
  actor_id: string | null;
  actor_role: string | null;
  target_type: string | null;
  target_id: string | null;
  metadata: Record<string, unknown>;
  diff: TrailDiff | null;
}

export interface ReviewerTrail {
  activity: TrailRow[];
  stats: { open: number; closed: number; total: number };
}
```

Append to `frontend/lib/api.ts` (following the existing `jsonFetch`/`base()` style):

```ts
// ---------------------------------------------------------------- assignments

export async function listMyBucket(): Promise<{ assignments: ReviewAssignment[]; total: number }> {
  return jsonFetch(`${base()}/assignments/my`);
}

export async function listAssignments(
  params: { status?: AssignmentStatus; assignee_id?: string } = {}
): Promise<{ assignments: ReviewAssignment[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.assignee_id) qs.set("assignee_id", params.assignee_id);
  const suffix = qs.toString() ? `?${qs}` : "";
  return jsonFetch(`${base()}/assignments${suffix}`);
}

export async function assignSubmission(body: {
  submission_id: string;
  assignee_id: string;
  priority?: AssignmentPriority;
  due_at?: string | null;
  note?: string | null;
}): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function reassignAssignment(
  id: string, body: { assignee_id: string; note?: string | null }
): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/reassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function startAssignment(id: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/start`, { method: "POST" });
}

export async function completeAssignment(id: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/complete`, { method: "POST" });
}

export async function sendBackAssignment(id: string, reason: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/send-back`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export async function cancelAssignment(id: string, reason?: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export async function assignmentWorkload(): Promise<{ reviewers: WorkloadRow[] }> {
  return jsonFetch(`${base()}/assignments/workload`);
}

export async function assignmentForSubmission(
  submissionId: string
): Promise<{ active: ReviewAssignment | null; history: ReviewAssignment[] }> {
  return jsonFetch(`${base()}/assignments/for-submission/${submissionId}`);
}

export async function documentTrail(submissionId: string): Promise<{ trail: TrailRow[] }> {
  return jsonFetch(`${base()}/assignments/trail/document/${submissionId}`);
}

export async function reviewerTrail(userId: string): Promise<ReviewerTrail> {
  return jsonFetch(`${base()}/assignments/trail/reviewer/${userId}`);
}
```

Add the new type names to the existing `import type { ... }` block at the top of `api.ts`.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add frontend/lib/types.ts frontend/lib/api.ts
```

---

## Task 14: Submissions list — role-aware tabs and the assign dialog

**Files:**
- Create: `frontend/components/assignments/AssignDialog.tsx`
- Create: `frontend/components/assignments/BucketTabs.tsx`
- Modify: `frontend/app/(workspace)/page.tsx` (the submissions list)

**Interfaces:**
- Consumes: Task 13's client functions; `useAuth()` for `me.role`
- Produces: `<AssignDialog submissionId onAssigned />`, `<BucketTabs value onChange counts />`

- [ ] **Step 1: Write the failing check**

Expected after the change:
1. A reviewer sees tabs `My bucket / In review / Awaiting sign-off / My uploads`.
2. An admin sees `All / Unassigned / In review / Awaiting sign-off / By reviewer`.
3. An admin's row menu offers Assign; a reviewer's does not.
4. The assign dialog lists reviewers with their open counts, lowest first.

- [ ] **Step 2: Confirm it fails**

Run `npm run dev`, open `/`. No tabs and no assign action exist yet.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/components/assignments/AssignDialog.tsx
"use client";
import { useEffect, useState } from "react";
import { assignSubmission, assignmentWorkload } from "@/lib/api";
import type { AssignmentPriority, WorkloadRow } from "@/lib/types";
import { Button } from "@/components/ui/button";

const PRIORITIES: AssignmentPriority[] = ["low", "normal", "high", "urgent"];

export function AssignDialog({
  submissionId,
  onAssigned,
  onClose,
}: {
  submissionId: string;
  onAssigned: () => void;
  onClose: () => void;
}) {
  const [reviewers, setReviewers] = useState<WorkloadRow[]>([]);
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState<AssignmentPriority>("normal");
  const [due, setDue] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Backend already sorts by open_count ascending — least loaded first, so
    // the default choice spreads work rather than piling it on one person.
    assignmentWorkload().then(r => setReviewers(r.reviewers)).catch(() => setReviewers([]));
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await assignSubmission({
        submission_id: submissionId,
        assignee_id: assignee,
        priority,
        due_at: due ? new Date(due).toISOString() : null,
        note: note.trim() || null,
      });
      onAssigned();
      onClose();
    } catch (e) {
      // 409 = already assigned. Say what to do about it rather than echoing
      // the status code.
      setError(
        e instanceof Error && e.message.includes("409")
          ? "This document is already assigned. Reassign it from the document page instead."
          : "Could not assign. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[440px] rounded-lg border border-border bg-background p-5 shadow-lg">
        <h2 className="text-base font-semibold">Assign for review</h2>

        <label className="mt-4 block text-xs font-medium text-muted-foreground">Reviewer</label>
        <select
          className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          value={assignee}
          onChange={e => setAssignee(e.target.value)}
        >
          <option value="">Select a reviewer…</option>
          {reviewers.filter(r => r.is_active).map(r => (
            <option key={r.user_id} value={r.user_id}>
              {r.username} — {r.open_count} open
            </option>
          ))}
        </select>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-muted-foreground">Priority</label>
            <select
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              value={priority}
              onChange={e => setPriority(e.target.value as AssignmentPriority)}
            >
              {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-muted-foreground">Due</label>
            <input
              type="date"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              value={due}
              onChange={e => setDue(e.target.value)}
            />
          </div>
        </div>

        <label className="mt-3 block text-xs font-medium text-muted-foreground">
          Note to the reviewer
        </label>
        <textarea
          className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          rows={3}
          value={note}
          onChange={e => setNote(e.target.value)}
        />

        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={!assignee || busy}>
            {busy ? "Assigning…" : "Assign"}
          </Button>
        </div>
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/assignments/BucketTabs.tsx
"use client";
import { cn } from "@/lib/utils";

export type BucketTab =
  | "my_bucket" | "in_review" | "awaiting_signoff" | "my_uploads"
  | "all" | "unassigned" | "by_reviewer";

const REVIEWER_TABS: { key: BucketTab; label: string }[] = [
  { key: "my_bucket", label: "My bucket" },
  { key: "in_review", label: "In review" },
  { key: "awaiting_signoff", label: "Awaiting sign-off" },
  { key: "my_uploads", label: "My uploads" },
];

const ADMIN_TABS: { key: BucketTab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unassigned", label: "Unassigned" },
  { key: "in_review", label: "In review" },
  { key: "awaiting_signoff", label: "Awaiting sign-off" },
  { key: "by_reviewer", label: "By reviewer" },
];

export function BucketTabs({
  role, value, counts, onChange,
}: {
  role: string;
  value: BucketTab;
  counts?: Partial<Record<BucketTab, number>>;
  onChange: (t: BucketTab) => void;
}) {
  const tabs = role === "user" ? REVIEWER_TABS : ADMIN_TABS;
  return (
    <div className="flex gap-1 border-b border-border" role="tablist">
      {tabs.map(t => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
          className={cn(
            "px-3 py-2 text-sm border-b-2 -mb-px transition-colors",
            value === t.key
              ? "border-primary text-foreground font-medium"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          {t.label}
          {counts?.[t.key] !== undefined && (
            <span className="ml-1.5 text-xs text-muted-foreground">{counts[t.key]}</span>
          )}
        </button>
      ))}
    </div>
  );
}
```

In `frontend/app/(workspace)/page.tsx`, render `<BucketTabs>` above the existing
table using `me.role`, hold the active tab in state, and filter the rows the page
already fetches. Add an Assign action to each row, rendered only when
`me.role !== "user"`, opening `<AssignDialog>`. Keep the page's existing data
fetching — the backend already scopes what comes back, so the tabs filter within
the visible set rather than issuing new privileged queries.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit` — expected: no errors.
Run: `npm run dev`, then check each expectation from Step 1 as a reviewer and as an admin.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add frontend/components/assignments/ "frontend/app/(workspace)/page.tsx"
```

---

## Task 15: Assignment banner and history panel on the submission page

**Files:**
- Create: `frontend/components/assignments/AssignmentBanner.tsx`
- Create: `frontend/components/assignments/HistoryPanel.tsx`
- Modify: `frontend/app/(workspace)/submissions/[id]/page.tsx`

**Interfaces:**
- Consumes: `assignmentForSubmission`, `documentTrail`, the lifecycle mutators (Task 13)
- Produces: `<AssignmentBanner submissionId />`, `<HistoryPanel submissionId />`

- [ ] **Step 1: Write the failing check**

Expected after the change:
1. A document with an assignment shows who holds it, its status, and its due date.
2. The assignee sees Start when `open`, Mark complete when `in_review`, and nothing actionable when `awaiting_signoff`.
3. An admin sees Send back and Approve when `awaiting_signoff`, and Reassign at any active state.
4. The history panel shows edits with a before/after, and is absent for a role without `trail:view`.

- [ ] **Step 2: Confirm it fails**

Open a submission — no banner, no history.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/components/assignments/AssignmentBanner.tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import {
  assignmentForSubmission, completeAssignment, sendBackAssignment, startAssignment,
} from "@/lib/api";
import type { ReviewAssignment } from "@/lib/types";
import { useAuth } from "@/components/auth/AuthProvider";
import { Button } from "@/components/ui/button";

const LABEL: Record<string, string> = {
  open: "Assigned",
  in_review: "In review",
  awaiting_signoff: "Awaiting sign-off",
  closed: "Closed",
  superseded: "Reassigned",
  cancelled: "Cancelled",
};

export function AssignmentBanner({ submissionId }: { submissionId: string }) {
  const { me } = useAuth();
  const [active, setActive] = useState<ReviewAssignment | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    assignmentForSubmission(submissionId)
      .then(r => setActive(r.active))
      .catch(() => setActive(null));
  }, [submissionId]);

  useEffect(load, [load]);

  if (!active) return null;

  const isAssignee = me?.id === active.assignee_id;
  const isAdmin = me?.role !== "user";

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); load(); } finally { setBusy(false); }
  }

  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 text-sm">
      <span className="font-medium">{LABEL[active.status] ?? active.status}</span>
      {active.due_at && (
        <span className="text-muted-foreground">
          due {new Date(active.due_at).toLocaleDateString()}
        </span>
      )}
      {active.priority !== "normal" && (
        <span className="rounded bg-background px-1.5 py-0.5 text-xs">{active.priority}</span>
      )}
      {active.note && <span className="truncate text-muted-foreground">“{active.note}”</span>}

      <div className="ml-auto flex gap-2">
        {isAssignee && active.status === "open" && (
          <Button size="sm" disabled={busy}
            onClick={() => run(() => startAssignment(active.id))}>Start review</Button>
        )}
        {isAssignee && active.status === "in_review" && (
          <Button size="sm" disabled={busy}
            onClick={() => run(() => completeAssignment(active.id))}>Mark complete</Button>
        )}
        {isAdmin && active.status === "awaiting_signoff" && (
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => {
              const reason = window.prompt("Why is this going back?");
              if (reason?.trim()) run(() => sendBackAssignment(active.id, reason));
            }}>Send back</Button>
        )}
      </div>
    </div>
  );
}
```

```tsx
// frontend/components/assignments/HistoryPanel.tsx
"use client";
import { useEffect, useState } from "react";
import { documentTrail } from "@/lib/api";
import type { TrailRow } from "@/lib/types";

const VERB: Record<string, string> = {
  assignment_created: "assigned",
  assignment_reassigned: "reassigned",
  assignment_started: "started review",
  assignment_completed: "completed review",
  assignment_sent_back: "sent back",
  assignment_closed: "closed",
  assignment_cancelled: "cancelled",
  submission_edited: "edited",
  submission_approved: "approved",
  submission_approval_override: "approved with override",
  comment_created: "commented",
  comment_updated: "edited a comment",
  comment_deleted: "deleted a comment",
  export_generated: "exported",
  violation_action_submitted: "actioned a finding",
};

export function HistoryPanel({ submissionId }: { submissionId: string }) {
  const [rows, setRows] = useState<TrailRow[] | null>(null);

  useEffect(() => {
    // 403 for a role without trail:view — render nothing rather than an error.
    documentTrail(submissionId).then(r => setRows(r.trail)).catch(() => setRows(null));
  }, [submissionId]);

  if (!rows) return null;
  if (rows.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No activity recorded yet.</p>;
  }

  return (
    <ol className="divide-y divide-border">
      {rows.map(r => (
        <li key={r.id} className="px-4 py-3 text-sm">
          <div className="flex items-baseline gap-2">
            <span className="text-xs tabular-nums text-muted-foreground">
              {r.at ? new Date(r.at).toLocaleString() : "—"}
            </span>
            <span className="font-medium">{VERB[r.event_type] ?? r.event_type}</span>
            {r.actor_role && (
              <span className="text-xs text-muted-foreground">({r.actor_role})</span>
            )}
          </div>
          {r.diff && (
            <div className="mt-1.5 space-y-0.5 font-mono text-xs">
              <div className="text-destructive">− {r.diff.before.slice(0, 200)}</div>
              <div className="text-emerald-600 dark:text-emerald-400">
                + {r.diff.after.slice(0, 200)}
              </div>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
```

Mount `<AssignmentBanner submissionId={id} />` above the document pane in
`app/(workspace)/submissions/[id]/page.tsx`, and `<HistoryPanel submissionId={id} />`
in the existing right-hand context rail as a new "History" section.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit` — expected: no errors.
Run the app and walk one document through assign → start → complete → send back →
complete → approve, checking each expectation from Step 1.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add frontend/components/assignments/ "frontend/app/(workspace)/submissions/[id]/page.tsx"
```

---

## Task 16: Reviewers page and audit feed upgrade

**Files:**
- Create: `frontend/app/(workspace)/reviewers/page.tsx`
- Create: `frontend/app/(workspace)/reviewers/[id]/page.tsx`
- Modify: `frontend/app/(super-admin)/super_admin/audit/page.tsx`
- Modify: `frontend/components/workspace/Sidebar.tsx` (add the Reviewers link)

**Interfaces:**
- Consumes: `assignmentWorkload`, `reviewerTrail` (Task 13)
- Produces: none

- [ ] **Step 1: Write the failing check**

Expected after the change:
1. `/reviewers` lists reviewers with open and closed counts; reachable by admin **and** super_admin.
2. `/reviewers/{id}` shows that person's activity, newest first, with diffs on edits.
3. `/super_admin/audit` has actor / event-type / date filters and renders verbs rather than raw JSON.

The Reviewers page lives in the workspace, not the console, because admin holds
`trail:view` but not `console:view` — putting it under `/super_admin` would gate
it away from the role that asked for it.

- [ ] **Step 2: Confirm it fails**

`/reviewers` 404s.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/app/(workspace)/reviewers/page.tsx
"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { assignmentWorkload } from "@/lib/api";
import type { WorkloadRow } from "@/lib/types";

export default function ReviewersPage() {
  const [rows, setRows] = useState<WorkloadRow[]>([]);

  useEffect(() => {
    assignmentWorkload().then(r => setRows(r.reviewers)).catch(() => setRows([]));
  }, []);

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-xl font-semibold">Reviewers</h1>
      <table className="w-full text-sm">
        <thead className="text-left text-muted-foreground">
          <tr className="border-b border-border">
            <th className="py-2 font-medium">Reviewer</th>
            <th className="py-2 font-medium">Role</th>
            <th className="py-2 font-medium">Open</th>
            <th className="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.user_id} className="border-b border-border last:border-0">
              <td className="py-2">
                <Link href={`/reviewers/${r.user_id}`} className="hover:underline">
                  {r.username}
                </Link>
              </td>
              <td className="py-2 text-muted-foreground">{r.role}</td>
              <td className="py-2 tabular-nums">{r.open_count}</td>
              <td className="py-2 text-muted-foreground">
                {r.is_active ? "active" : "inactive"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="text-sm text-muted-foreground">No reviewers yet.</p>
      )}
    </div>
  );
}
```

```tsx
// frontend/app/(workspace)/reviewers/[id]/page.tsx
"use client";
import { use, useEffect, useState } from "react";
import { reviewerTrail } from "@/lib/api";
import type { ReviewerTrail } from "@/lib/types";

export default function ReviewerDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<ReviewerTrail | null>(null);

  useEffect(() => {
    reviewerTrail(id).then(setData).catch(() => setData(null));
  }, [id]);

  if (!data) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;

  return (
    <div className="p-6 space-y-6">
      <div className="flex gap-6 text-sm">
        <span><strong className="tabular-nums">{data.stats.open}</strong> open</span>
        <span><strong className="tabular-nums">{data.stats.closed}</strong> closed</span>
        <span><strong className="tabular-nums">{data.stats.total}</strong> total</span>
      </div>

      <ol className="divide-y divide-border rounded-md border border-border">
        {data.activity.map(r => (
          <li key={r.id} className="px-4 py-3 text-sm">
            <div className="flex items-baseline gap-2">
              <span className="text-xs tabular-nums text-muted-foreground">
                {r.at ? new Date(r.at).toLocaleString() : "—"}
              </span>
              <span className="font-medium">{r.event_type}</span>
            </div>
            {r.diff && (
              <div className="mt-1.5 space-y-0.5 font-mono text-xs">
                <div className="text-destructive">− {r.diff.before.slice(0, 200)}</div>
                <div className="text-emerald-600 dark:text-emerald-400">
                  + {r.diff.after.slice(0, 200)}
                </div>
              </div>
            )}
          </li>
        ))}
      </ol>
      {data.activity.length === 0 && (
        <p className="text-sm text-muted-foreground">No recorded activity.</p>
      )}
    </div>
  );
}
```

Add to `Sidebar.tsx`'s Admin section (already gated on `ADMIN_ROLES`, which is
admin + super_admin — the same set that holds `trail:view`):

```tsx
      { label: "Reviewers", href: "/reviewers", icon: <Users className="h-3.5 w-3.5" /> },
```

Import `Users` from `lucide-react`.

For `/super_admin/audit/page.tsx`, add three controlled inputs (actor substring,
event-type select built from the distinct types present, and a date cutoff),
filter `feed` client-side, and replace the `JSON.stringify(a.details)` cell with
the same `VERB` map used by `HistoryPanel` plus a `<details>` disclosure holding
the raw payload for when someone genuinely needs it.

- [ ] **Step 4: Verify**

Run: `cd frontend && npx tsc --noEmit` — expected: no errors.
Run the app: check `/reviewers` as admin and as super_admin, and confirm a
plain `user` gets an empty/denied view rather than data.

- [ ] **Step 5: Stage (ask before committing)**

```bash
git add "frontend/app/(workspace)/reviewers/" \
        "frontend/app/(super-admin)/super_admin/audit/page.tsx" \
        frontend/components/workspace/Sidebar.tsx
```

---

## Task 17: Migration and rollout

Runs only after Tasks 1–16 are green. Ordering matters: the code tolerates the
new column being absent, but not the reverse.

**Files:** none — this is an operational task.

- [ ] **Step 1: Check the deployed migration state**

Run: `cd backend && alembic current`
Expected: `0037`. `submissions.py:714` carries a comment claiming two migrations
were once unapplied on the deployed system. If `alembic current` reports
anything below `0037`, **stop** and report the gap — do not stack `0038` on an
unknown baseline.

- [ ] **Step 2: Back up first**

Run: `cd backend && bash scripts/db_backup.sh`
Expected: a dump file written. `0038` is additive and its `downgrade()` is
complete, but the backup is cheap and the table is new.

- [ ] **Step 3: Apply**

Run: `cd backend && alembic upgrade head`
Expected: `Running upgrade 0037 -> 0038`.

- [ ] **Step 4: Verify the constraint actually exists**

This is the one assertion the test suite could not make (no Postgres in tests),
so make it here against the real database:

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'review_assignments';
```

Expected: `uq_review_assignments_active` present, `UNIQUE`, with
`WHERE (status = ANY (ARRAY['open','in_review','awaiting_signoff']))`.

Then prove it bites:

```sql
INSERT INTO review_assignments (id, submission_id, assignee_id, status)
VALUES (gen_random_uuid(), '<some-submission-id>', '<some-user-id>', 'open');
-- repeat the same statement; the second must fail with a unique violation
ROLLBACK;
```

- [ ] **Step 5: Rebuild and roll out**

The image bakes the code, so a restart alone ships nothing:

```bash
docker compose build backend
docker compose up -d backend
```

Then, before telling users: an admin must triage the **Unassigned** queue.
Until they do, every reviewer's list is empty by design — legacy submissions
have `submitted_by = NULL` and no assignment, and there was never any ownership
data to backfill from.

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §1 `review_assignments`, partial unique index, `scope_submission_id` | 3 |
| §2 lifecycle, reassignment, send-back | 5, 9 |
| §3 visibility, 404-not-403, legacy consequence | 6, 7, 17 |
| §4 permissions, hierarchy, ownership-vs-permission, deactivated reviewers | 2, 9 (`workload` reports `is_active`) |
| §5 workspace redirect, cross-nav, `must_change_password` gap | 12 |
| §6 `record_sync`, missing emissions | 4, 10 |
| §7 `trail_service` | 11 |
| §8 screens | 14, 15, 16 |
| §9 testing | every task |
| §10 deploy notes | 17 |

**Deviations from the spec, deliberate and recorded**

1. The partial unique index is not asserted at the database level in tests — the suite has no Postgres. Covered by a service-level guard (Task 5), a migration-source assertion (Task 3), and a live check at rollout (Task 17, Step 4). Recorded at the top of this plan.
2. Route coverage is asserted statically (Task 7) rather than as 26 request tests. Stronger, not weaker: a new unguarded route fails the suite even though nobody wrote a test for it.

**Type consistency** — `ACTIVE_STATUSES` is a tuple defined once in `review_assignment.py` and imported by `assignment_service`, `visibility`, `trail_service`, and the router. The `_dict()` shape in `assignments.py` matches the `ReviewAssignment` TypeScript interface field for field. `TrailRow` matches `trail_service._row()`. `document_trail`/`reviewer_trail` names are identical across service, route, and client.

**Task 1 dependency note** — every backend test task imports `tests.support.fake_session`, so Task 1 must land first. Tasks 2 and 3 are independent of each other; 4 needs 3; 5 needs 3+4; 6 needs 3; 7 needs 6; 9 needs 5+6; 10 needs 4; 11 needs 3; 12–16 need 9+11 for their endpoints.
