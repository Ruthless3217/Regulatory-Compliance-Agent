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

Numbering note: this feature was planned as 0038, but 0038-0040
(layer_all_corpora / retrieval_queries / retrieval_evaluations) landed from the
concurrent corpus-layers workstream while it was being built, so it chains off
0040 instead. Only one migration may claim down_revision "0040" — two heads is
what crash-looped the backend at 0018.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: Union[str, None] = "0040"
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
