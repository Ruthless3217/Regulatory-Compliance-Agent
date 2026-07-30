"""Reviewer-action taxonomy — widen rule_feedback into the audit trail behind
Correct / Not-a-violation / Dismiss, and link both rule_feedback and violations
back to the AnalysisRun that produced them.

`rule_feedback.verdict` was `String(10)` ("accept"/"reject" only, 6/7 chars);
the richer taxonomy value `'not_violation'` is 13 chars, so it is widened to
`String(20)`. Everything else here is a new nullable column — snapshots of the
finding's context at review time (confidence, retrieval, product, model/KB
identity) plus queue-routing bookkeeping for a review-queue UI, and a
`submission_id`/`analysis_run_id` pair so a reviewer action can be traced back
to the exact run without joining through `violations` -> `compliance_checks`.

`violations` gains `analysis_run_id` (which run produced this finding),
`review_status`, and `resolved_at` so a violation's reviewer-facing lifecycle
is queryable without a `rule_feedback` join.

All additive/nullable, zero backfill.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- rule_feedback: widen verdict, add the reviewer-action taxonomy ---
    op.alter_column(
        "rule_feedback", "verdict",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
    )
    op.add_column("rule_feedback", sa.Column("reason", sa.String(length=40), nullable=True))
    op.add_column("rule_feedback", sa.Column("original_text", sa.Text(), nullable=True))
    op.add_column("rule_feedback", sa.Column("suggested_text", sa.Text(), nullable=True))
    op.add_column("rule_feedback", sa.Column("final_text", sa.Text(), nullable=True))
    op.add_column("rule_feedback", sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rule_feedback", sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rule_feedback", sa.Column("confidence_snapshot", sa.Float(), nullable=True))
    op.add_column("rule_feedback", sa.Column("retrieval_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("rule_feedback", sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("rule_feedback", sa.Column("model_version", sa.String(length=100), nullable=True))
    op.add_column("rule_feedback", sa.Column("kb_version", sa.String(length=100), nullable=True))
    op.add_column("rule_feedback", sa.Column("routed_queue", sa.String(length=30), nullable=True))
    op.add_column("rule_feedback", sa.Column("queue_resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rule_feedback", sa.Column("queue_resolved_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rule_feedback", sa.Column("queue_resolved_note", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_rule_feedback_submission_id", "rule_feedback", "submissions",
        ["submission_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_rule_feedback_analysis_run_id", "rule_feedback", "analysis_runs",
        ["analysis_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_rule_feedback_queue_resolved_by", "rule_feedback", "users",
        ["queue_resolved_by"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_rule_feedback_submission_id", "rule_feedback", ["submission_id"])
    op.create_index("ix_rule_feedback_analysis_run_id", "rule_feedback", ["analysis_run_id"])
    op.create_index("ix_rule_feedback_routed_queue", "rule_feedback", ["routed_queue"])

    # --- violations: run provenance + reviewer lifecycle ---
    op.add_column("violations", sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("violations", sa.Column("review_status", sa.String(length=20), nullable=True))
    op.add_column("violations", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_violations_analysis_run_id", "violations", "analysis_runs",
        ["analysis_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_violations_analysis_run_id", "violations", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_violations_analysis_run_id", table_name="violations")
    op.drop_constraint("fk_violations_analysis_run_id", "violations", type_="foreignkey")
    op.drop_column("violations", "resolved_at")
    op.drop_column("violations", "review_status")
    op.drop_column("violations", "analysis_run_id")

    op.drop_index("ix_rule_feedback_routed_queue", table_name="rule_feedback")
    op.drop_index("ix_rule_feedback_analysis_run_id", table_name="rule_feedback")
    op.drop_index("ix_rule_feedback_submission_id", table_name="rule_feedback")
    op.drop_constraint("fk_rule_feedback_queue_resolved_by", "rule_feedback", type_="foreignkey")
    op.drop_constraint("fk_rule_feedback_analysis_run_id", "rule_feedback", type_="foreignkey")
    op.drop_constraint("fk_rule_feedback_submission_id", "rule_feedback", type_="foreignkey")
    op.drop_column("rule_feedback", "queue_resolved_note")
    op.drop_column("rule_feedback", "queue_resolved_by")
    op.drop_column("rule_feedback", "queue_resolved_at")
    op.drop_column("rule_feedback", "routed_queue")
    op.drop_column("rule_feedback", "kb_version")
    op.drop_column("rule_feedback", "model_version")
    op.drop_column("rule_feedback", "product_snapshot")
    op.drop_column("rule_feedback", "retrieval_snapshot")
    op.drop_column("rule_feedback", "confidence_snapshot")
    op.drop_column("rule_feedback", "analysis_run_id")
    op.drop_column("rule_feedback", "submission_id")
    op.drop_column("rule_feedback", "final_text")
    op.drop_column("rule_feedback", "suggested_text")
    op.drop_column("rule_feedback", "original_text")
    op.drop_column("rule_feedback", "reason")
    op.alter_column(
        "rule_feedback", "verdict",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
    )
