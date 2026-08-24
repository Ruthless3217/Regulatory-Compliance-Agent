"""retrieval_queries — one row per hybrid_search call, and retention for both
candidate tables.

`retrieval_candidates` (0037) records candidates the store RETURNED. It cannot
record what the relevance floors removed, because `rag_min_cosine` and
`rag_min_ts_rank` are predicates inside the leg SQL: a row below either floor is
not in any result set, so nothing downstream can see it. That left the two
settings most directly controlling recall as the only part of retrieval with no
evidence attached — precisely where tuning happens.

This table carries the per-query evidence that IS free to collect: how full the
recall pool came back, and the weakest candidate that survived. A pool short of
`recall_pool` means something truncated it; a weakest-surviving cosine sitting on
top of the floor means the floor is binding. Neither needs a second scan.

Retention: a 40-chunk document across 5 rule categories emits roughly 4,600
candidate rows and 250 query rows per run, and nothing pruned them. Both tables
get a `created_at` index so `prune_retrieval_trace()` is a range delete rather
than a full scan.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Which chunk asked. Text, not UUID: the same column in
        # retrieval_candidates is text, and chunk ids are not always UUIDs.
        sa.Column("chunk_id", sa.Text(), nullable=True),
        # Applicability vocabulary ("rules" / "precedents" / "product_docs"),
        # matching retrieval_candidates.corpus so the two join cleanly.
        sa.Column("corpus", sa.Text(), nullable=False),
        # The physical table. Kept separately because one corpus name can map to
        # two tables (precedent_cases and the legacy rag_compliance_examples),
        # and which one answered is the whole point on a fallback run.
        sa.Column("index_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("recall_pool", sa.Integer(), nullable=True),
        sa.Column("vector_returned", sa.Integer(), nullable=True),
        sa.Column("keyword_returned", sa.Integer(), nullable=True),
        sa.Column("fused_total", sa.Integer(), nullable=True),
        # False means the BM25 leg was skipped (no query text), which is not the
        # same as running and matching nothing.
        sa.Column("keyword_leg_ran", sa.Boolean(), nullable=True),
        sa.Column("min_cosine_seen", sa.Float(), nullable=True),
        sa.Column("max_cosine_seen", sa.Float(), nullable=True),
        sa.Column("min_ts_rank_seen", sa.Float(), nullable=True),
        # The floors in force for THIS query. Stored per row, not read from
        # settings at display time, so a run stays interpretable after the
        # thresholds are retuned.
        sa.Column("min_cosine_floor", sa.Float(), nullable=True),
        sa.Column("min_ts_rank_floor", sa.Float(), nullable=True),
        sa.Column("pool_truncated", sa.Boolean(), nullable=True),
        # Retriever-supplied context, e.g. product_scope_not_pushed on the
        # legacy precedent index, which has no scope column to filter on.
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_queries_run", "retrieval_queries", ["run_id"])
    op.create_index("ix_retrieval_queries_corpus", "retrieval_queries", ["corpus"])
    op.create_index("ix_retrieval_queries_created", "retrieval_queries", ["created_at"])

    # Retention support for the table 0037 created without it.
    op.create_index(
        "ix_retrieval_candidates_created", "retrieval_candidates", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_candidates_created", table_name="retrieval_candidates")
    op.drop_index("ix_retrieval_queries_created", table_name="retrieval_queries")
    op.drop_index("ix_retrieval_queries_corpus", table_name="retrieval_queries")
    op.drop_index("ix_retrieval_queries_run", table_name="retrieval_queries")
    op.drop_table("retrieval_queries")
