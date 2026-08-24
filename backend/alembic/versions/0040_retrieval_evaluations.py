"""retrieval_evaluations — judged relevance scores for a run's retrieval.

Everything else in the retrieval trace is mechanical: what was retrieved, what
ranked where, what the floors did. None of it can answer "was the retrieved
context actually relevant to this chunk" — that is a judgement, and judgements
cost a model call.

So they are stored, not recomputed. An evaluation is an expensive, explicitly
requested artefact of one run at one moment; re-deriving it on page load would
bill the user for scrolling. Keeping the evaluator and model on every row means a
score stays interpretable after the judge is upgraded, and lets two evaluators be
compared on the same run instead of silently replacing each other.

`score` is nullable on purpose: a metric that errors or abstains records the row
with a reason and a NULL score, which is the honest representation. Dropping the
row instead would make a failed evaluation indistinguishable from one that was
never run.

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Applicability vocabulary, matching retrieval_candidates.corpus.
        sa.Column("corpus", sa.Text(), nullable=False),
        # NULL means the score is for the run as a whole rather than one chunk.
        sa.Column("chunk_id", sa.Text(), nullable=True),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # Who judged, and with what. A score without these is not comparable to
        # any other score.
        sa.Column("evaluator", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        # How many retrieved passages the metric saw. A 1.0 over one passage and
        # a 1.0 over twelve are not the same claim.
        sa.Column("contexts", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_evaluations_run", "retrieval_evaluations", ["run_id"])
    op.create_index(
        "ix_retrieval_evaluations_metric", "retrieval_evaluations", ["metric", "corpus"]
    )
    op.create_index("ix_retrieval_evaluations_created", "retrieval_evaluations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_evaluations_created", table_name="retrieval_evaluations")
    op.drop_index("ix_retrieval_evaluations_metric", table_name="retrieval_evaluations")
    op.drop_index("ix_retrieval_evaluations_run", table_name="retrieval_evaluations")
    op.drop_table("retrieval_evaluations")
