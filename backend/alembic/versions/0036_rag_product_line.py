"""product_line scope on the four product-bearing RAG corpora.

`rules.product_line` and `submissions.product_line` already exist (0009) and
`precedent_cases.product_category` since the precedent work — but the vector
tables the retrievers actually read carry no product scope at all, so
applicability can only be judged AFTER retrieval (or not at all). This adds the
column + a btree index so a later wave can push the filter down into the query.

Backfilled here only where a join is proof:
  rag_rules   ← rules.product_line        (rag_rules.id == rules.id)
  rag_chunks  ← submissions.product_line  (via submission_id)

rag_source_docs and rag_product_docs stay NULL: their scope comes from the
guideline filename and from the UIN → fact-card lookup respectively, i.e. from
files on disk. A migration that reads the filesystem would produce a different
schema on every machine, so that work lives in
scripts/backfill_product_metadata.py.

Nullable everywhere and never inferred: an unscoped row must stay visibly
unscoped for curation, not be guessed into a product family.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same width as rules.product_line / submissions.product_line (0009).
_TABLES = ("rag_source_docs", "rag_rules", "rag_chunks", "rag_product_docs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("product_line", sa.String(length=100), nullable=True))
        op.create_index(f"ix_{table}_product_line", table, ["product_line"])

    op.execute(
        "UPDATE rag_rules AS r SET product_line = s.product_line "
        "FROM rules AS s "
        "WHERE s.id = r.id AND s.product_line IS NOT NULL"
    )
    op.execute(
        "UPDATE rag_chunks AS c SET product_line = s.product_line "
        "FROM submissions AS s "
        "WHERE s.id = c.submission_id AND s.product_line IS NOT NULL"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_product_line", table_name=table)
        op.drop_column(table, "product_line")
