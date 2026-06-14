"""Rule versioning + product/jurisdiction scoping, violation citation locators +
rule_version snapshot + suppressed lane, and per-vector embedding identity.

Adds, all nullable/defaulted so it applies to an existing populated DB without
backfill pain:
  rules:        version, effective_date, superseded_by, product_line, jurisdiction
  submissions:  product_line, jurisdiction
  violations:   cited_section, cited_page, cited_regulation_version, rule_version,
                suppressed, suppressed_reason
  rag_* tables: embedding_model, embedding_dim  (so a stored vector can be tied
                to the model that produced it; query-time mismatch fails closed)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RAG_TABLES = ("rag_rules", "rag_chunks", "rag_source_docs", "rag_compliance_examples")


def upgrade() -> None:
    # --- rules: versioning + scoping ---
    op.add_column("rules", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("rules", sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("rules", sa.Column("superseded_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rules", sa.Column("product_line", sa.String(length=100), nullable=True))
    op.add_column("rules", sa.Column("jurisdiction", sa.String(length=100), nullable=True))
    op.create_foreign_key(
        "fk_rules_superseded_by", "rules", "rules",
        ["superseded_by"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_rules_superseded_by", "rules", ["superseded_by"])
    op.create_index("ix_rules_product_line", "rules", ["product_line"])
    op.create_index("ix_rules_jurisdiction", "rules", ["jurisdiction"])

    # --- submissions: scoping ---
    op.add_column("submissions", sa.Column("product_line", sa.String(length=100), nullable=True))
    op.add_column("submissions", sa.Column("jurisdiction", sa.String(length=100), nullable=True))
    op.create_index("ix_submissions_product_line", "submissions", ["product_line"])
    op.create_index("ix_submissions_jurisdiction", "submissions", ["jurisdiction"])

    # --- violations: citation locators + rule_version snapshot + suppressed lane ---
    op.add_column("violations", sa.Column("cited_section", sa.Text(), nullable=True))
    op.add_column("violations", sa.Column("cited_page", sa.Integer(), nullable=True))
    op.add_column("violations", sa.Column("cited_regulation_version", sa.String(length=50), nullable=True))
    op.add_column("violations", sa.Column("rule_version", sa.Integer(), nullable=True))
    op.add_column(
        "violations",
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("violations", sa.Column("suppressed_reason", sa.Text(), nullable=True))
    op.create_index("ix_violations_suppressed", "violations", ["suppressed"])

    # --- rag_* tables: per-vector embedding identity ---
    for tbl in _RAG_TABLES:
        op.add_column(tbl, sa.Column("embedding_model", sa.Text(), nullable=True))
        op.add_column(tbl, sa.Column("embedding_dim", sa.Integer(), nullable=True))


def downgrade() -> None:
    for tbl in _RAG_TABLES:
        op.drop_column(tbl, "embedding_dim")
        op.drop_column(tbl, "embedding_model")

    op.drop_index("ix_violations_suppressed", table_name="violations")
    op.drop_column("violations", "suppressed_reason")
    op.drop_column("violations", "suppressed")
    op.drop_column("violations", "rule_version")
    op.drop_column("violations", "cited_regulation_version")
    op.drop_column("violations", "cited_page")
    op.drop_column("violations", "cited_section")

    op.drop_index("ix_submissions_jurisdiction", table_name="submissions")
    op.drop_index("ix_submissions_product_line", table_name="submissions")
    op.drop_column("submissions", "jurisdiction")
    op.drop_column("submissions", "product_line")

    op.drop_index("ix_rules_jurisdiction", table_name="rules")
    op.drop_index("ix_rules_product_line", table_name="rules")
    op.drop_index("ix_rules_superseded_by", table_name="rules")
    op.drop_constraint("fk_rules_superseded_by", "rules", type_="foreignkey")
    op.drop_column("rules", "jurisdiction")
    op.drop_column("rules", "product_line")
    op.drop_column("rules", "superseded_by")
    op.drop_column("rules", "effective_date")
    op.drop_column("rules", "version")
