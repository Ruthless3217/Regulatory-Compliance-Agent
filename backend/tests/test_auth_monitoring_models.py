"""Phase 1 (audit-trail): schema/model registration for auth + token/cost monitoring.

These assert the four new ORM models exist with the exact columns from the design
(`audit-trail/04-database-schema.md`) and that the existing `users` table gained the
auth columns. No DB connection is needed — this is pure metadata inspection.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_user_gained_auth_columns():
    from app.models.user import User

    columns = {c.name for c in User.__table__.columns}
    # existing columns preserved (firebase_uid is intentionally dropped)
    assert {"id", "email", "display_name", "role", "created_at"} <= columns
    assert "firebase_uid" not in columns
    # new auth columns added
    assert {
        "username", "password_hash", "registered_ip", "allowed_ips", "allowed_cidr",
        "is_active", "must_change_password", "created_by", "last_login_at",
        "password_updated_at",
    } <= columns


def test_user_session_model():
    from app.models.user_session import UserSession
    from app.database import Base

    assert UserSession.__tablename__ == "user_sessions"
    assert "user_sessions" in Base.metadata.tables
    assert {c.name for c in UserSession.__table__.columns} == {
        "id", "user_id", "ip", "user_agent", "login_at", "last_seen_at",
        "logout_at", "duration_seconds", "status",
    }


def test_analysis_run_model():
    from app.models.analysis_run import AnalysisRun
    from app.database import Base

    assert AnalysisRun.__tablename__ == "analysis_runs"
    assert "analysis_runs" in Base.metadata.tables
    assert {c.name for c in AnalysisRun.__table__.columns} == {
        "id", "submission_id", "triggered_by", "session_id", "run_number",
        "is_rerun", "trigger_source", "status", "compliance_check_id",
        "degraded_reason", "started_at", "finished_at", "duration_ms",
        "prompt_tokens", "completion_tokens", "total_tokens", "total_cost_usd",
    }


def test_llm_usage_event_model():
    from app.models.llm_usage_event import LlmUsageEvent
    from app.database import Base

    assert LlmUsageEvent.__tablename__ == "llm_usage_events"
    assert "llm_usage_events" in Base.metadata.tables
    assert {c.name for c in LlmUsageEvent.__table__.columns} == {
        "id", "user_id", "session_id", "submission_id", "run_id", "feature",
        "profile", "provider", "model", "prompt_tokens", "completion_tokens",
        "total_tokens", "input_cost_usd", "output_cost_usd", "total_cost_usd",
        "token_source", "price_source", "latency_ms", "is_retry", "created_at",
    }


def test_audit_event_model():
    from app.models.audit_event import AuditEvent
    from app.database import Base

    assert AuditEvent.__tablename__ == "audit_events"
    assert "audit_events" in Base.metadata.tables
    # note: the JSONB blob column is named "metadata" in the DB but mapped to a
    # non-reserved python attribute (SQLAlchemy reserves `.metadata`).
    assert {c.name for c in AuditEvent.__table__.columns} == {
        "id", "event_type", "actor_user_id", "actor_role", "actor_ip",
        "session_id", "target_type", "target_id", "before", "after",
        "metadata", "created_at",
    }
    # the reserved-name workaround must not shadow Base.metadata
    assert hasattr(AuditEvent, "event_metadata")


def test_new_models_registered_in_package():
    from app import models

    for name in ("UserSession", "AnalysisRun", "LlmUsageEvent", "AuditEvent"):
        assert name in models.__all__, f"{name} missing from models.__all__"
        assert hasattr(models, name)
