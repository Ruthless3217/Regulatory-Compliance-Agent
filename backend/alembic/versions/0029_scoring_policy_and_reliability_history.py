"""analysis_runs.scoring_policy_version + rule_reliability_events — the
missing audit trail behind `Rule.reliability_alpha/beta`.

Today a reviewer verdict mutates `rules.reliability_alpha/beta` IN PLACE
(rule_feedback_service.apply_feedback) with no history log — there is no way
to see how a rule's learned trust (theta = alpha/(alpha+beta)) evolved over
time, only its current value. `rule_reliability_events` is an append-only
before/after snapshot per verdict, keyed to the `rule_feedback` row that
caused it (nullable: a future direct/manual adjustment might not have one).

`analysis_runs.scoring_policy_version` stamps which scoring-policy revision
(new `settings.SCORING_POLICY_VERSION`) computed a given run's score, so a
later policy change doesn't silently make historic scores incomparable
without a record of which policy produced them.

All additive/nullable, zero backfill.

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("scoring_policy_version", sa.String(length=32), nullable=True))

    op.create_table(
        "rule_reliability_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_feedback_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alpha_before", sa.Float(), nullable=True),
        sa.Column("beta_before", sa.Float(), nullable=True),
        sa.Column("alpha_after", sa.Float(), nullable=True),
        sa.Column("beta_after", sa.Float(), nullable=True),
        sa.Column("theta_before", sa.Float(), nullable=True),
        sa.Column("theta_after", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rule_feedback_id"], ["rule_feedback.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_reliability_events_rule", "rule_reliability_events", ["rule_id", sa.text("created_at DESC")])
    op.create_index("ix_rule_reliability_events_feedback", "rule_reliability_events", ["rule_feedback_id"])


def downgrade() -> None:
    op.drop_index("ix_rule_reliability_events_feedback", table_name="rule_reliability_events")
    op.drop_index("ix_rule_reliability_events_rule", table_name="rule_reliability_events")
    op.drop_table("rule_reliability_events")

    op.drop_column("analysis_runs", "scoring_policy_version")
