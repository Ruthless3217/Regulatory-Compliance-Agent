"""lexical working document — an editable state beside the immutable original.

The uploaded file at submissions.file_path is never mutated. `lexical_state`
holds the Lexical node tree the reviewer actually edits; `lexical_html` holds
the same document as markup, serialized by the client from that same state so
server-side export never has to re-implement Lexical's serializer in Python.
The two are two views of one document: written together, never independently.

Both nullable, on purpose. Every existing submission and revision has neither,
and every existing code path has to keep working without them — import is
best-effort and falls back to the extracted-text behaviour.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("lexical_state", postgresql.JSONB(), nullable=True))
    op.add_column("submissions", sa.Column("lexical_html", sa.Text(), nullable=True))
    op.add_column("submission_revisions", sa.Column("lexical_state", postgresql.JSONB(), nullable=True))
    op.add_column("submission_revisions", sa.Column("lexical_html", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("submission_revisions", "lexical_html")
    op.drop_column("submission_revisions", "lexical_state")
    op.drop_column("submissions", "lexical_html")
    op.drop_column("submissions", "lexical_state")
