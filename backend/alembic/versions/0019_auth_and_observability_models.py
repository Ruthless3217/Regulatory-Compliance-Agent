"""add auth and observability models

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users ALTER
    op.add_column("users", sa.Column("username", sa.String(length=150), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("registered_ip", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("allowed_ips", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("users", sa.Column("allowed_cidr", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("users", sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_users_created_by_users", "users", "users", ["created_by"], ["id"], ondelete="SET NULL")
    
    op.execute("CREATE UNIQUE INDEX ix_users_username ON users (username) WHERE username IS NOT NULL;")

    # email was created NOT NULL by 0001, but auth accounts are keyed on username
    # (email is optional / reserved for future SSO — the model is nullable). Relax
    # it so username-only users (super-admin, graders, admins) can be provisioned.
    op.alter_column("users", "email", existing_type=sa.String(length=500), nullable=True)

    # 2. user_sessions
    op.create_table(
        "user_sessions",
        # id is the opaque session token (token_urlsafe), not a UUID — it mirrors
        # the Redis session id set on the rca_session cookie.
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=True),
        sa.Column("login_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_user_sessions_user", "user_sessions", ["user_id", sa.text("login_at DESC")])
    op.create_index("ix_user_sessions_status", "user_sessions", ["status"])

    # 3. analysis_runs
    op.create_table(
        "analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("is_rerun", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("trigger_source", sa.String(length=16), server_default="sync", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("compliance_check_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("degraded_reason", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["compliance_check_id"], ["compliance_checks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_analysis_runs_submission", "analysis_runs", ["submission_id", "run_number"])
    op.create_index("ix_analysis_runs_user", "analysis_runs", ["triggered_by", sa.text("started_at DESC")])
    op.create_index("ix_analysis_runs_started", "analysis_runs", [sa.text("started_at DESC")])

    # 4. llm_usage_events
    op.create_table(
        "llm_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("profile", sa.String(length=16), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("output_cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("total_cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("token_source", sa.String(length=12), server_default="measured", nullable=False),
        sa.Column("price_source", sa.String(length=12), server_default="configured", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_retry", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_usage_user_created", "llm_usage_events", ["user_id", sa.text("created_at DESC")])
    op.create_index("ix_usage_submission", "llm_usage_events", ["submission_id"])
    op.create_index("ix_usage_run", "llm_usage_events", ["run_id"])
    op.create_index("ix_usage_model", "llm_usage_events", ["model", "created_at"])
    op.create_index("ix_usage_created", "llm_usage_events", ["created_at"])

    # 5. audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("target_type", sa.String(length=24), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_audit_type_created", "audit_events", ["event_type", sa.text("created_at DESC")])
    op.create_index("ix_audit_actor_created", "audit_events", ["actor_user_id", sa.text("created_at DESC")])
    op.create_index("ix_audit_target", "audit_events", ["target_type", "target_id"])

    # Audit trigger
    op.execute('''
    CREATE OR REPLACE FUNCTION audit_events_no_mutate() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;
    ''')
    op.execute('''
    CREATE TRIGGER trg_audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION audit_events_no_mutate();
    ''')


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events;')
    op.execute('DROP FUNCTION IF EXISTS audit_events_no_mutate();')
    
    op.drop_table("audit_events")
    op.drop_table("llm_usage_events")
    op.drop_table("analysis_runs")
    op.drop_table("user_sessions")
    
    op.execute("DROP INDEX IF EXISTS ix_users_username;")
    op.alter_column("users", "email", existing_type=sa.String(length=500), nullable=False)
    op.drop_constraint("fk_users_created_by_users", "users", type_="foreignkey")
    op.drop_column("users", "password_updated_at")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "created_by")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_active")
    op.drop_column("users", "allowed_cidr")
    op.drop_column("users", "allowed_ips")
    op.drop_column("users", "registered_ip")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "username")
