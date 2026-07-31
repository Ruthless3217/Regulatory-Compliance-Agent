"""corpus_layers — provenance + an on/off switch for ingested corpus content.

Today every precedent lands in one undifferentiated `precedent_cases` pool with
no record of which ingest produced it. An admin cannot see what a given ingest
contributed, cannot disable a bad contribution, and cannot remove one document's
precedents without hand-written SQL.

`corpus_layers` is that missing provenance row, and `precedent_cases.source_layer_id`
is the (nullable) link back to it. Nullable is load-bearing: every precedent that
predates this migration keeps a NULL layer and MUST stay retrievable — retrieval
treats NULL as "always on" (see pgvector_store._LAYER_GUARD).

Disabling a layer is a boolean flip: instant, reversible, and it never touches the
embeddings. Deleting with purge=true is the only path that destroys vectors, and
`precedent_cases` IS the vector table, so deleting the row is the whole cleanup.

Additive/nullable, zero backfill.

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-31
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "corpus_layers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("ingested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ingested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_corpus_layers_name"),
    )
    # Retrieval reads this on every precedent query ("which layers are off?").
    op.create_index("ix_corpus_layers_enabled", "corpus_layers", ["enabled"])

    # precedent_cases is NOT a SQLAlchemy model — it is created and queried via
    # raw SQL (migration 0012 / rag/stores/pgvector_store.py), so the column is
    # added against the table name directly.
    op.add_column(
        "precedent_cases",
        sa.Column("source_layer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_precedent_cases_source_layer",
        "precedent_cases",
        "corpus_layers",
        ["source_layer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_precedent_cases_source_layer", "precedent_cases", ["source_layer_id"])


def downgrade() -> None:
    op.drop_index("ix_precedent_cases_source_layer", table_name="precedent_cases")
    op.drop_constraint("fk_precedent_cases_source_layer", "precedent_cases", type_="foreignkey")
    op.drop_column("precedent_cases", "source_layer_id")

    op.drop_index("ix_corpus_layers_enabled", table_name="corpus_layers")
    op.drop_table("corpus_layers")
