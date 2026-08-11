"""chunk-level analysis reuse keys on content_chunks.

`content_hash` is the sha256 of the chunk's text exactly as stored: it is how
preprocessing tells an edited document from an untouched one, and which chunk
rows may keep their id across a re-chunk. `context_key` is stamped by a run
that actually persisted its findings and says under WHICH prompt/model/corpus
those findings were produced — an equal key on the same row means the previous
verdicts still stand and the chunk can skip its LLM passes. See
services/agents/compliance/analysis_cache.py.

Both nullable: every existing chunk predates the feature and simply never hits
the cache. `content_hash` IS backfilled (one statement, Postgres' built-in
sha256 matches hashlib.sha256 over the same UTF-8 bytes), so re-analysing an
old submission doesn't needlessly churn every chunk id and re-index its
vectors. `context_key` is deliberately NOT backfilled — no historical run
recorded the conditions it graded under, and guessing would serve stale
verdicts.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("content_chunks", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("content_chunks", sa.Column("context_key", sa.String(64), nullable=True))
    op.execute(
        "UPDATE content_chunks "
        "SET content_hash = encode(sha256(convert_to(text, 'UTF8')), 'hex') "
        "WHERE content_hash IS NULL"
    )
    op.create_index(
        "ix_content_chunks_submission_hash",
        "content_chunks",
        ["submission_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_chunks_submission_hash", table_name="content_chunks")
    op.drop_column("content_chunks", "context_key")
    op.drop_column("content_chunks", "content_hash")
