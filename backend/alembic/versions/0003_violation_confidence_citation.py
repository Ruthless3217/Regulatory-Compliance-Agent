"""Violation confidence + regulator citation columns.

Adds two columns required for financial-grade auditability:
- confidence: LLM-reported confidence in [0,1] per violation
- regulator_quote: verbatim source-passage quote backing the violation

Also normalizes existing rows' severity/category to lowercase so dashboard
aggregations stop double-counting "CRITICAL" vs "critical".

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "violations",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="0.85",
        ),
    )
    op.add_column(
        "violations",
        sa.Column("regulator_quote", sa.Text(), nullable=True),
    )

    # Backfill: normalize severity + category casing so aggregations stop
    # splitting "CRITICAL" vs "critical" into separate rows.
    op.execute("UPDATE violations SET severity = lower(severity) WHERE severity IS NOT NULL")
    op.execute("UPDATE violations SET category = lower(category) WHERE category IS NOT NULL")
    op.execute("UPDATE violations SET auto_fixable = lower(auto_fixable) WHERE auto_fixable IS NOT NULL")


def downgrade() -> None:
    op.drop_column("violations", "regulator_quote")
    op.drop_column("violations", "confidence")
