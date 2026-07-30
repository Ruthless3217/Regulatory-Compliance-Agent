"""violations.fix_applied / fix_applied_at — has this finding's suggested fix
already been written into the document (via a submission_revisions entry)?

Third independent column-set landing on `violations` (after 0023's
analysis_run_id/review_status/resolved_at and 0024's anchor columns) — no name
collision with either.

Additive/nullable except the boolean flag itself, which defaults false so
every existing violation reads as "not yet applied" with zero backfill.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "violations",
        sa.Column("fix_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("violations", sa.Column("fix_applied_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("violations", "fix_applied_at")
    op.drop_column("violations", "fix_applied")
