"""submission_revisions — the one mutation primitive behind manual edits,
apply-fix, bulk-apply-fixes, and restore.

Each row is a full-content snapshot (not a diff) so restoring a past revision
or diffing two revisions never needs to replay a patch chain. `source`
records which of the four UI actions produced it; `applied_violation_ids`
records which findings the "apply fix" / "bulk apply" actions consumed so the
UI can show a violation as already-applied. `revision_number` is 1-based per
submission (UNIQUE(submission_id, revision_number)) so callers can address a
specific past version without knowing its UUID.

New table, zero backfill.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "submission_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # manual_edit | apply_fix | bulk_apply_fixes | restore
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("applied_violation_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "revision_number", name="uq_submission_revisions_submission_number"),
    )
    op.create_index("ix_submission_revisions_submission", "submission_revisions", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_submission_revisions_submission", table_name="submission_revisions")
    op.drop_table("submission_revisions")
