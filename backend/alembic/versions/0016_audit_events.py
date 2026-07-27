"""audit_events — append-only who-did-what log + immutability trigger.

The application only ever INSERTs into audit_events. A BEFORE UPDATE OR DELETE
trigger raises, so tracks can't be altered even from the app DB role (belt-and-
suspenders with the least-privilege DB role added in Phase 7 hardening).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("target_type", sa.String(length=24), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_type_created", "audit_events", ["event_type", "created_at"])
    op.create_index("ix_audit_actor_created", "audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_target", "audit_events", ["target_type", "target_id"])

    # Append-only enforcement.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_no_mutate() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'audit_events is append-only'; END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_no_update
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_no_mutate();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS audit_events_no_mutate();")
    op.drop_table("audit_events")
