"""document_comparisons — persisted diff results for the standalone Compare tool.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("old_content_type", sa.String(length=50), nullable=False),
        sa.Column("new_content_type", sa.String(length=50), nullable=False),
        sa.Column("old_file_path", sa.String(length=1000), nullable=True),
        sa.Column("new_file_path", sa.String(length=1000), nullable=True),
        sa.Column("old_original_content", sa.Text(), nullable=True),
        sa.Column("new_original_content", sa.Text(), nullable=True),
        sa.Column("diff_result", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="processing"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_document_comparisons_status", "document_comparisons", ["status"])


def downgrade() -> None:
    op.drop_index("ix_document_comparisons_status", table_name="document_comparisons")
    op.drop_table("document_comparisons")
