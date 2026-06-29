"""precedent_cases — anchored-comment precedent records (pgvector + tsvector).

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-22
"""
import os
from typing import Sequence, Union
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        CREATE TABLE precedent_cases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_hash TEXT NOT NULL UNIQUE,
            highlighted_span TEXT NOT NULL,
            span_context TEXT,
            reviewer_comment TEXT NOT NULL,
            reviewer_role TEXT,
            is_reviewer BOOLEAN NOT NULL DEFAULT FALSE,
            thread JSONB,
            resolved BOOLEAN NOT NULL DEFAULT FALSE,
            before_text TEXT,
            after_text TEXT,
            regulation_tags JSONB,
            issue_type TEXT,
            why_rationale TEXT,
            guideline_ref TEXT,
            severity TEXT,
            product_category TEXT,
            ticket TEXT,
            source_file TEXT,
            comment_date TIMESTAMPTZ,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            example_tickets JSONB,
            embed_text TEXT NOT NULL,
            embedding VECTOR({EMBED_DIM}) NOT NULL,
            embedding_model TEXT,
            embedding_dim INTEGER,
            search_tsv TSVECTOR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.create_index("ix_precedent_cases_issue_type", "precedent_cases", ["issue_type"])
    op.create_index("ix_precedent_cases_severity", "precedent_cases", ["severity"])
    op.create_index("ix_precedent_cases_ticket", "precedent_cases", ["ticket"])
    op.execute("CREATE INDEX ix_precedent_cases_tsv ON precedent_cases USING GIN (search_tsv)")
    op.execute(
        "CREATE INDEX ix_precedent_cases_embedding ON precedent_cases "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION precedent_cases_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', COALESCE(NEW.highlighted_span, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.reviewer_comment, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(NEW.span_context, '')), 'C');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER precedent_cases_tsv_trg BEFORE INSERT OR UPDATE ON precedent_cases "
        "FOR EACH ROW EXECUTE FUNCTION precedent_cases_tsv_trigger()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS precedent_cases_tsv_trg ON precedent_cases")
    op.execute("DROP FUNCTION IF EXISTS precedent_cases_tsv_trigger")
    op.drop_table("precedent_cases")
