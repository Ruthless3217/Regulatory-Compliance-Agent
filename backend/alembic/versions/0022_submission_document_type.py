"""submission document_type — semantic document classification.

Adds a nullable document_type to submissions (Workstream A, 2026-07-15). Gates
the product mandatory-element (UIN / regulatory descriptor) obligations: only
product_marketing (or an unset/unknown value → strict) requires them, so a
blog/article is no longer asked for a UIN. Additive + nullable; existing rows
read as NULL → strict.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("document_type", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_submissions_document_type", "submissions", ["document_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_document_type", table_name="submissions")
    op.drop_column("submissions", "document_type")
