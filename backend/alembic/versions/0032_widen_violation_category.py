"""violations.category 50 -> 100 — an LLM-chosen label must not cost a run.

`severity` is validated against an allow-list before insert; `category` never
was. It is free text the model picks per finding ("claim settlement ratio
disclosure & approval" is 44 characters, and that one fit). On 2026-07-31 a
label finally exceeded 50, psycopg2 raised StringDataRightTruncation on the
batch INSERT, and because every violation goes in one statement the failure
discarded ALL 108 findings of a completed analysis — after the full LLM spend.

Widening buys headroom for the labels actually observed. It is deliberately
paired with a hard clamp at the write site (ComplianceEngine._CATEGORY_MAX_LEN):
widening alone would only move the cliff, since nothing bounds what the model
can emit. The clamp guarantees the run survives; the wider column means a
realistic label survives intact rather than being cut.

Not a data migration: existing values are <= 50 chars and remain valid.
The downgrade is guarded — it refuses rather than silently truncating rows that
have since exceeded 50.

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "violations", "category",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Refuse rather than corrupt: narrowing silently truncates any row that
    # used the new headroom, and these are audit records.
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text("SELECT count(*) FROM violations WHERE length(category) > 50")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"Refusing to narrow violations.category: {too_long} row(s) exceed "
            f"50 chars and would be truncated. Re-label or delete them first."
        )
    op.alter_column(
        "violations", "category",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
