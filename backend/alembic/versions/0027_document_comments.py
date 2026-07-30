"""document_comments — freestanding reviewer notes anchored to a text
selection, mirroring `comparison_annotations`' shape but for a single
submission's document (not a two-side comparison).

Unlike `comparison_annotations` (keyed to a stable `change_id`), a document
comment anchors to `anchor_text` (the selected substring) since a submission's
document has no equivalent stable per-change id — `page_number` is the page
the selection was made on (nullable: plain-text documents have no pages).

New table, zero backfill.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_comments_submission", "document_comments", ["submission_id"])
    op.create_index("ix_document_comments_resolved", "document_comments", ["resolved"])


def downgrade() -> None:
    op.drop_index("ix_document_comments_resolved", table_name="document_comments")
    op.drop_index("ix_document_comments_submission", table_name="document_comments")
    op.drop_table("document_comments")
