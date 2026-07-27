"""usage_monitoring — user_sessions, analysis_runs, llm_usage_events.

The durable session record (for session-time reporting), the per-run fact table
(covers fail-closed runs that persist no ComplianceCheck), and the per-LLM-call
usage ledger (input/output tokens + USD cost, fully attributed).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- user_sessions (PK id = the opaque server session id / sid) ----------
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("login_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.create_index("ix_user_sessions_user", "user_sessions", ["user_id", "login_at"])
    op.create_index("ix_user_sessions_status", "user_sessions", ["status"])

    # --- analysis_runs -------------------------------------------------------
    op.create_table(
        "analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggered_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("is_rerun", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trigger_source", sa.String(length=16), nullable=False, server_default="sync"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column(
            "compliance_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_checks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("degraded_reason", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_analysis_runs_submission", "analysis_runs", ["submission_id", "run_number"])
    op.create_index("ix_analysis_runs_user", "analysis_runs", ["triggered_by", "started_at"])
    op.create_index("ix_analysis_runs_started", "analysis_runs", ["started_at"])

    # --- llm_usage_events (highest-volume: one row per billed LLM call) -------
    op.create_table(
        "llm_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("profile", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("output_cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("token_source", sa.String(length=12), nullable=False, server_default="measured"),
        sa.Column("price_source", sa.String(length=12), nullable=False, server_default="configured"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_retry", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_usage_user_created", "llm_usage_events", ["user_id", "created_at"])
    op.create_index("ix_usage_submission", "llm_usage_events", ["submission_id"])
    op.create_index("ix_usage_run", "llm_usage_events", ["run_id"])
    op.create_index("ix_usage_model", "llm_usage_events", ["model", "created_at"])
    op.create_index("ix_usage_created", "llm_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_usage_events")
    op.drop_table("analysis_runs")
    op.drop_table("user_sessions")
