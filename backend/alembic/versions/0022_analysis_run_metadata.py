"""analysis_runs.run_metadata — durable retrieval/observability payload.

The dispatch node now records WHY every retrieved candidate entered or was
refused from the compliance context (retrieval scope, per-candidate verdicts,
grounding mix — RETRIEVAL_RCA.md §4). That payload previously lived only in the
in-memory graph state and evaporated when the run finished; a JSONB column on
analysis_runs makes every verdict's retrieval story queryable after the fact.

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("run_metadata", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "run_metadata")
