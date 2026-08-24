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
    / "alembic" / "versions" / "0041_review_assignments_and_trail.py"
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
    """0038-0040 were taken mid-build by the concurrent corpus-layers work, so
    this feature lands at 0041 rather than the 0038 the plan first assumed."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0041"' in src
    assert 'down_revision: Union[str, None] = "0040"' in src


def test_is_the_only_head():
    """A second migration claiming down_revision "0040" would give Alembic two
    heads and crash-loop the backend on startup — this repo has hit exactly
    that before (migration 0018)."""
    versions = MIGRATION.parent
    claimants = [
        p.name for p in versions.glob("*.py")
        if 'down_revision: Union[str, None] = "0040"' in p.read_text(encoding="utf-8")
    ]
    assert claimants == [MIGRATION.name], f"multiple heads off 0040: {claimants}"
