"""submissions.current_content — the editable working copy.

`original_content` is the immutable submitted text: grading and the plain-text
highlighter both key off it and must keep doing so. `current_content` is the
document AFTER reviewer edits/apply-fix/restore have been applied — starts
NULL (meaning "no edits yet, current == original") and is written by the
revisions API (migration 0026) as edits land.

Additive/nullable, zero backfill.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("current_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "current_content")
