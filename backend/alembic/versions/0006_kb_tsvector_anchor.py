"""rag_compliance_examples — include anchor_text in the tsvector.

Reviewers often anchor comments on a literal phrase ("guaranteed returns").
Semantic-only recall smooths those over; weighting anchor_text alongside
chunk_text in search_tsv lets BM25 retrieve precedents that quote the same
literal phrase as the new draft. Backfills existing rows.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-28
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rag_compliance_examples_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', COALESCE(NEW.chunk_text, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.anchor_text, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.comment_text, '')), 'B');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        UPDATE rag_compliance_examples
           SET search_tsv =
                 setweight(to_tsvector('english', COALESCE(chunk_text, '')), 'A') ||
                 setweight(to_tsvector('english', COALESCE(anchor_text, '')), 'A') ||
                 setweight(to_tsvector('english', COALESCE(comment_text, '')), 'B')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rag_compliance_examples_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', COALESCE(NEW.chunk_text, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.comment_text, '')), 'B');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        UPDATE rag_compliance_examples
           SET search_tsv =
                 setweight(to_tsvector('english', COALESCE(chunk_text, '')), 'A') ||
                 setweight(to_tsvector('english', COALESCE(comment_text, '')), 'B')
        """
    )
