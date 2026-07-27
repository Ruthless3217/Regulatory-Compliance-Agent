"""violation grouping columns — merge overlapping cross-tier findings.

Adds group_id + is_primary to violations (Workstream C, 2026-07-15). Overlapping
findings on one span share a group_id; the strongest (is_primary=true) is scored,
the rest are kept for display ("list all angles"). Both are additive and nullable/
defaulted, so existing rows read as standalone primaries.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-15
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
        "violations",
        sa.Column("group_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "violations",
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.create_index("ix_violations_group_id", "violations", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_violations_group_id", table_name="violations")
    op.drop_column("violations", "is_primary")
    op.drop_column("violations", "group_id")
