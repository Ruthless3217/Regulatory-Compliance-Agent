"""Initial schema — mirrors current create_all() across all 10 models.

Revision ID: 0001
Revises:
Create Date: 2026-05-18
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(500), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("firebase_uid", sa.String(200), unique=True, nullable=True),
        sa.Column("role", sa.String(50), server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_firebase_uid", "users", ["firebase_uid"])

    # submissions
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("original_content", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("status", sa.String(50), server_default="uploaded"),
        sa.Column("approval_status", sa.String(50), server_default="pending", nullable=False),
    )

    # rules
    op.create_table(
        "rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("keywords", postgresql.JSONB(), nullable=True),
        sa.Column("pattern", sa.String(1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("rule_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("points_deduction", sa.Numeric(5, 2), nullable=False, server_default="-5.00"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_auto_generated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("generated_from_industry", sa.String(100), nullable=True),
        sa.Column("generation_source", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
    )
    op.create_index("ix_rules_category", "rules", ["category"])
    op.create_index("ix_rules_severity", "rules", ["severity"])
    op.create_index("ix_rules_created_by", "rules", ["created_by"])
    op.create_index("ix_rules_is_auto_generated", "rules", ["is_auto_generated"])
    op.create_index("ix_rules_generated_from_industry", "rules", ["generated_from_industry"])

    # compliance_checks
    op.create_table(
        "compliance_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("grade", sa.String(10), nullable=True),
        sa.Column("status", sa.String(50), server_default="completed"),
        sa.Column("scores", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_compliance_checks_submission_id", "compliance_checks", ["submission_id"])

    # violations
    op.create_table(
        "violations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("compliance_check_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("compliance_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("current_text", sa.Text(), nullable=True),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("auto_fixable", sa.String(5), server_default="false"),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("violation_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_violations_compliance_check_id", "violations", ["compliance_check_id"])
    op.create_index("ix_violations_rule_id", "violations", ["rule_id"])

    # content_chunks
    op.create_table(
        "content_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("chunk_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_content_chunks_submission_id", "content_chunks", ["submission_id"])

    # agent_executions
    op.create_table(
        "agent_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_type", sa.String(100), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), server_default="running"),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_time_ms", sa.String(20), nullable=True),
        sa.Column("total_tokens_used", sa.String(20), nullable=True),
    )

    # agent_traces
    op.create_table(
        "agent_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.String(50), nullable=True),
        sa.Column("thought", sa.Text(), nullable=True),
        sa.Column("action", sa.String(200), nullable=True),
        sa.Column("action_input", postgresql.JSONB(), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_traces_execution_id", "agent_traces", ["execution_id"])

    # tool_invocations
    op.create_table(
        "tool_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tool_invocations_execution_id", "tool_invocations", ["execution_id"])

    # compliance_states
    op.create_table(
        "compliance_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("chunks", postgresql.JSONB(), nullable=True),
        sa.Column("active_rules", postgresql.JSONB(), nullable=True),
        sa.Column("violations", postgresql.JSONB(), nullable=True),
        sa.Column("active_agents", postgresql.JSONB(), nullable=True),
        sa.Column("scores", postgresql.JSONB(), nullable=True),
        sa.Column("messages", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_compliance_states_submission_id", "compliance_states", ["submission_id"])


def downgrade() -> None:
    op.drop_table("compliance_states")
    op.drop_table("tool_invocations")
    op.drop_table("agent_traces")
    op.drop_table("agent_executions")
    op.drop_table("content_chunks")
    op.drop_table("violations")
    op.drop_table("compliance_checks")
    op.drop_table("rules")
    op.drop_table("submissions")
    op.drop_table("users")
