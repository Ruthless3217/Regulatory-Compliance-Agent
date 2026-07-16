"""comparison filenames — store the original uploaded file names per side

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_comparisons",
        sa.Column("old_filename", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "document_comparisons",
        sa.Column("new_filename", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_comparisons", "new_filename")
    op.drop_column("document_comparisons", "old_filename")
