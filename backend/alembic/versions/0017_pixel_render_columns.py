"""pixel render columns — page-image overlay model for the Compare tool.

Revision ID: 0017
Revises: 0013
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_comparisons", sa.Column("render_result", postgresql.JSONB(), nullable=True))
    op.add_column(
        "document_comparisons",
        sa.Column("render_status", sa.String(length=50), nullable=False, server_default="processing"),
    )
    op.add_column("document_comparisons", sa.Column("render_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_comparisons", "render_error")
    op.drop_column("document_comparisons", "render_status")
    op.drop_column("document_comparisons", "render_result")
