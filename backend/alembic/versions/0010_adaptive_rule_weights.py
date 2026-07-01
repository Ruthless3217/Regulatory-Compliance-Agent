"""Adaptive rule weights (Beta-Binomial reliability + HITL feedback).

Adds, all nullable so it applies to a populated DB with zero behavior change
until feedback starts flowing (NULL counts → θ = 1.0 → today's scores):

 rules: reliability_alpha, reliability_beta
 (Beta pseudo-counts behind θ = α/(α+β), the learned
 probability that a finding fired by this rule is correct)
 compliance_checks: reviewer_score, reviewer_scored_at
 (held-out evaluation ONLY — never an input to scoring or
 weight updates; drives the convergence curve
 |system − reviewer| over time)
 rule_feedback: one reviewer verdict per (violation, reviewer) — the
 audit trail behind every weight update

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
 # --- rules: learned reliability pseudo-counts ---
 op.add_column("rules", sa.Column("reliability_alpha", sa.Numeric(10, 2), nullable=True))
 op.add_column("rules", sa.Column("reliability_beta", sa.Numeric(10, 2), nullable=True))

 # --- compliance_checks: held-out reviewer score ---
 op.add_column("compliance_checks", sa.Column("reviewer_score", sa.Float(), nullable=True))
 op.add_column(
 "compliance_checks",
 sa.Column("reviewer_scored_at", sa.DateTime(timezone=True), nullable=True),
 )

 # --- rule_feedback: the HITL audit trail ---
 op.create_table(
 "rule_feedback",
 sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
 sa.Column(
 "violation_id",
 postgresql.UUID(as_uuid=True),
 sa.ForeignKey("violations.id", ondelete="CASCADE"),
 nullable=False,
 ),
 sa.Column(
 "rule_id",
 postgresql.UUID(as_uuid=True),
 sa.ForeignKey("rules.id", ondelete="SET NULL"),
 nullable=True,
 ),
 sa.Column("verdict", sa.String(length=10), nullable=False),
 sa.Column("severity_override", sa.String(length=20), nullable=True),
 sa.Column(
 "reviewer_id",
 postgresql.UUID(as_uuid=True),
 sa.ForeignKey("users.id", ondelete="SET NULL"),
 nullable=True,
 ),
 sa.Column("comment", sa.Text(), nullable=True),
 sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
 sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
 sa.UniqueConstraint(
 "violation_id", "reviewer_id", name="uq_rule_feedback_violation_reviewer"
 ),
 )
 op.create_index("ix_rule_feedback_violation_id", "rule_feedback", ["violation_id"])
 op.create_index("ix_rule_feedback_rule_id", "rule_feedback", ["rule_id"])
 op.create_index("ix_rule_feedback_reviewer_id", "rule_feedback", ["reviewer_id"])


def downgrade() -> None:
 op.drop_index("ix_rule_feedback_reviewer_id", table_name="rule_feedback")
 op.drop_index("ix_rule_feedback_rule_id", table_name="rule_feedback")
 op.drop_index("ix_rule_feedback_violation_id", table_name="rule_feedback")
 op.drop_table("rule_feedback")

 op.drop_column("compliance_checks", "reviewer_scored_at")
 op.drop_column("compliance_checks", "reviewer_score")

 op.drop_column("rules", "reliability_beta")
 op.drop_column("rules", "reliability_alpha")
