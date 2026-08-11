"""retrieval_candidates — per-chunk, per-candidate accept/reject tracing.

`analysis_runs.run_metadata['retrieval_debug']` (0022) answers "what did the
applicability judge decide", but it is capped at 100 rejections + 100
acceptances per run and carries only the fused score. It cannot answer the
question a curator actually asks: *why is this chunk's rule not in the prompt,
and which stage dropped it* — the cosine leg, the ts_rank leg, the fusion rank,
the per-chunk rule cap and the retriever's own filters all leave no trace.

One row per candidate per (chunk, category) query, written once at the end of
dispatch_node. `verdict` is NULL exactly when the applicability judge never saw
the row (it lost the fusion, or a retriever filter dropped it first) — that is
the honest value, not "accepted".

Deliberately NOT a SQLAlchemy model: the table is written by one bulk INSERT
and read by one admin route, both raw SQL, and an ORM mapping would only add a
second definition to keep in sync.

Volume is bounded per query, not truncated per run: the trace keeps the fused
top_k plus the next 10 near-misses (rag/trace.py NEAR_MISS), so the row count is
explainable ("top_k + 10 per query") rather than an unlabelled sample of an
unknown population. CASCADE on the run so purging a run purges its trace.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable: the flat active-rule fallback set is validated once per run,
        # not per chunk, so those rows genuinely belong to no chunk.
        sa.Column("chunk_id", sa.String(length=64), nullable=True),
        sa.Column("corpus", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        # Not an FK: ids span rules, precedent_cases and the legacy
        # rag_compliance_examples index, and a purged referent must not delete
        # the evidence that it was once retrieved.
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("retrieval_method", sa.String(length=8), nullable=True),  # vector|bm25|both
        sa.Column("cosine", sa.Float(), nullable=True),
        sa.Column("ts_rank", sa.Float(), nullable=True),
        sa.Column("vector_rank", sa.Integer(), nullable=True),
        sa.Column("bm25_rank", sa.Integer(), nullable=True),
        sa.Column("fused_score", sa.Float(), nullable=True),
        sa.Column("fused_rank", sa.Integer(), nullable=True),
        # {"index": <table queried>, "applied": <filter dict or null>}
        sa.Column("filters", postgresql.JSONB(), nullable=True),
        sa.Column("scope_value", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("final_status", sa.String(length=32), nullable=False),
        sa.Column("deciding_stage", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_candidates_run", "retrieval_candidates", ["run_id"])
    op.create_index("ix_retrieval_candidates_run_chunk", "retrieval_candidates", ["run_id", "chunk_id"])
    op.create_index("ix_retrieval_candidates_run_status", "retrieval_candidates", ["run_id", "final_status"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_candidates_run_status", table_name="retrieval_candidates")
    op.drop_index("ix_retrieval_candidates_run_chunk", table_name="retrieval_candidates")
    op.drop_index("ix_retrieval_candidates_run", table_name="retrieval_candidates")
    op.drop_table("retrieval_candidates")
