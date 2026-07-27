"""auth_users — add authentication columns to users.

Adds username/password_hash/IP-binding/activation/bootstrap columns for the
audit-trail auth layer. All additive and nullable/defaulted → reversible with no
backfill. email is relaxed to NULLABLE (login is by username; email is optional
display/notification metadata).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=150), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("registered_ip", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("allowed_ips", postgresql.JSONB(), nullable=True))
    op.add_column("users", sa.Column("allowed_cidr", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True))

    # login handle is unique when set; existing NULL-username rows are exempt.
    op.create_index(
        "ix_users_username",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("username IS NOT NULL"),
    )

    # email becomes optional (username is the login handle now).
    op.alter_column("users", "email", existing_type=sa.String(length=500), nullable=True)


def downgrade() -> None:
    # best-effort: restoring NOT NULL fails if any NULL email exists (pre-auth
    # rows always had one, so this is safe in practice).
    op.alter_column("users", "email", existing_type=sa.String(length=500), nullable=False)
    op.drop_index("ix_users_username", table_name="users")
    for col in (
        "password_updated_at",
        "last_login_at",
        "created_by",
        "must_change_password",
        "is_active",
        "allowed_cidr",
        "allowed_ips",
        "registered_ip",
        "password_hash",
        "username",
    ):
        op.drop_column("users", col)
