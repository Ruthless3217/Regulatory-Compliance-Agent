"""rag_compliance_examples — precedent reviewer-decision vectors (pgvector + tsvector).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25
"""
import os
from typing import Sequence, Union
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same env-driven dim as the existing three indexes (openai=1536, cohere=1024).
EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))


def upgrade() -> None:
 op.execute("CREATE EXTENSION IF NOT EXISTS vector")

 op.execute(
 f"""
 CREATE TABLE rag_compliance_examples (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 document_id TEXT NOT NULL,
 title TEXT,
 task TEXT,
 section_label TEXT,
 chunk_text TEXT NOT NULL,
 anchor_text TEXT,
 reviewer_name TEXT,
 comment_text TEXT NOT NULL,
 final_text_chunk TEXT,
 violation_category TEXT,
 severity TEXT,
 source_file TEXT NOT NULL,
 embed_text TEXT NOT NULL,
 embedding VECTOR({EMBED_DIM}) NOT NULL,
 search_tsv TSVECTOR,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
 )
 """
 )
 op.create_index("ix_rag_ce_reviewer", "rag_compliance_examples", ["reviewer_name"])
 op.create_index("ix_rag_ce_category", "rag_compliance_examples", ["violation_category"])
 op.create_index("ix_rag_ce_severity", "rag_compliance_examples", ["severity"])
 op.create_index("ix_rag_ce_document_id", "rag_compliance_examples", ["document_id"])
 op.create_index("ix_rag_ce_source_file", "rag_compliance_examples", ["source_file"])
 op.execute(
 "CREATE INDEX ix_rag_ce_tsv ON rag_compliance_examples USING GIN (search_tsv)"
 )
 op.execute(
 "CREATE INDEX ix_rag_ce_embedding ON rag_compliance_examples "
 "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
 )

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
 "CREATE TRIGGER rag_compliance_examples_tsv_trg "
 "BEFORE INSERT OR UPDATE ON rag_compliance_examples "
 "FOR EACH ROW EXECUTE FUNCTION rag_compliance_examples_tsv_trigger()"
 )


def downgrade() -> None:
 op.execute(
 "DROP TRIGGER IF EXISTS rag_compliance_examples_tsv_trg ON rag_compliance_examples"
 )
 op.execute("DROP FUNCTION IF EXISTS rag_compliance_examples_tsv_trigger")
 op.drop_table("rag_compliance_examples")
 # Don't drop the vector extension — other tables use it.
