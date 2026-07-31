"""violations.source / violations.created_by — who authored this finding.

Until now every row in `violations` was a model prediction, so authorship was
implicit and unrecorded. Letting a reviewer flag arbitrary text (including text
the model never surfaced — the coverage gap the `novel` tier documents) breaks
that assumption: the table now holds two different kinds of row and the
difference is load-bearing.

It matters most for MODEL-PRECISION math (model_learning.py). A
reviewer-written flag is not a prediction the model made; counting it as one
would inflate precision and corrupt the exact number used to judge whether the
model is improving — the same anti-Goodhart reasoning that keeps
`reviewer_score` held out of training (rule_feedback_service.py).

`source` is NOT NULL with server_default 'model', so every pre-existing row
reads as model-authored with zero backfill — that IS the backfill. `created_by`
stays NULL for them (nobody authored them) and ON DELETE SET NULL so removing a
user never deletes a compliance finding.

Fourth independent column-set on `violations` (after 0023, 0024, 0028) — no
name collision with any of them.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-31
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "violations",
        sa.Column("source", sa.String(length=16), nullable=False, server_default=sa.text("'model'")),
    )
    op.add_column(
        "violations",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_violations_created_by",
        "violations",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    # Every precision/rate query in model_learning.py now filters on it.
    op.create_index("ix_violations_source", "violations", ["source"])


def downgrade() -> None:
    op.drop_index("ix_violations_source", table_name="violations")
    op.drop_constraint("fk_violations_created_by", "violations", type_="foreignkey")
    op.drop_column("violations", "created_by")
    op.drop_column("violations", "source")
