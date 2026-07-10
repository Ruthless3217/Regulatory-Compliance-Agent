"""drop compliance_states table and firebase_uid column.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("compliance_states")
    op.drop_index("ix_compliance_states_submission_id", table_name="compliance_states", if_exists=True)
    op.drop_column("users", "firebase_uid")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("firebase_uid", sa.String(length=200), nullable=True),
    )
    op.create_index("ix_users_firebase_uid", "users", ["firebase_uid"], unique=True)
    op.create_table(
        "compliance_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("chunks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("violations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active_agents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_states_submission_id", "compliance_states", ["submission_id"])
