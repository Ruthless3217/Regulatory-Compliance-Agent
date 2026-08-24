"""source_layer_id on every layered corpus — make the layer off-switch real.

Migration 0030 gave `precedent_cases` a `source_layer_id` and taught the store
to hide rows belonging to a disabled layer. It stopped there, and that left the
feature governing one table out of five:

* `rag_compliance_examples` is the v1 precedent corpus, and
  `precedent_retriever` falls back to it whenever `precedent_cases` is empty —
  the state of any deployment restored from a pre-v2 backup. On that path the
  off switch was bypassed entirely.
* `rag_rules`, `rag_source_docs` and `rag_product_docs` had no provenance and
  no switch at all, so a bad rules import or a superseded brochure could only be
  removed by hand-written DELETEs.

Additive and nullable, exactly as 0030 was, and for the same load-bearing
reason: every existing row keeps a NULL layer and MUST stay retrievable. The
store's guard reads `source_layer_id IS NULL` first, so NULL means "always on".

Zero backfill. Nothing is re-embedded; the column sits beside the vector.

`rag_chunks` is intentionally excluded — it holds the submission under review,
not corpus. See services/rag/corpus_registry.py.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# precedent_cases already has the column (0030); these are the four that don't.
_TABLES = (
    "rag_compliance_examples",
    "rag_rules",
    "rag_source_docs",
    "rag_product_docs",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("source_layer_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_source_layer",
            table,
            "corpus_layers",
            ["source_layer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        # Retrieval filters on this column on every single query, and the admin
        # counts rows by it; without the index both are sequential scans over
        # the whole corpus.
        op.create_index(f"ix_{table}_source_layer", table, ["source_layer_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_source_layer", table_name=table)
        op.drop_constraint(f"fk_{table}_source_layer", table, type_="foreignkey")
        op.drop_column(table, "source_layer_id")
