"""RAG tables — rag_rules, rag_chunks, rag_source_docs with pgvector + tsvector.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Embedding dim is provider-dependent. Read from env so swapping
# RAG_EMBEDDING_PROVIDER (openai=1536, cohere=1024, azure_openai=1536) only
# requires an env change + fresh DB volume, not a migration edit.
EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))


def upgrade() -> None:
 # Enable pgvector
 op.execute("CREATE EXTENSION IF NOT EXISTS vector")

 # rag_rules
 op.execute(
 f"""
 CREATE TABLE rag_rules (
 id UUID PRIMARY KEY,
 category VARCHAR(50) NOT NULL,
 severity VARCHAR(20) NOT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 rule_text TEXT NOT NULL,
 keywords JSONB,
 embed_text TEXT NOT NULL,
 embedding VECTOR({EMBED_DIM}) NOT NULL,
 search_tsv TSVECTOR,
 source TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
 )
 """
 )
 op.create_index("ix_rag_rules_category", "rag_rules", ["category"])
 op.create_index("ix_rag_rules_severity", "rag_rules", ["severity"])
 op.create_index("ix_rag_rules_is_active", "rag_rules", ["is_active"])
 op.execute("CREATE INDEX ix_rag_rules_tsv ON rag_rules USING GIN (search_tsv)")
 op.execute(
 "CREATE INDEX ix_rag_rules_embedding ON rag_rules "
 "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
 )

 # rag_chunks (current + past submissions)
 op.execute(
 f"""
 CREATE TABLE rag_chunks (
 id UUID PRIMARY KEY,
 submission_id UUID NOT NULL,
 chunk_index INTEGER NOT NULL,
 page_number INTEGER,
 text TEXT NOT NULL,
 embedding VECTOR({EMBED_DIM}) NOT NULL,
 search_tsv TSVECTOR,
 submission_status VARCHAR(30) NOT NULL DEFAULT 'analyzing',
 submission_summary TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
 )
 """
 )
 op.create_index("ix_rag_chunks_submission_id", "rag_chunks", ["submission_id"])
 op.create_index("ix_rag_chunks_submission_status", "rag_chunks", ["submission_status"])
 op.execute("CREATE INDEX ix_rag_chunks_tsv ON rag_chunks USING GIN (search_tsv)")
 op.execute(
 "CREATE INDEX ix_rag_chunks_embedding ON rag_chunks "
 "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
 )

 # rag_source_docs
 op.execute(
 f"""
 CREATE TABLE rag_source_docs (
 id UUID PRIMARY KEY,
 document_id UUID NOT NULL,
 document_title TEXT NOT NULL,
 regulator VARCHAR(50) NOT NULL,
 chunk_index INTEGER NOT NULL,
 page_number INTEGER,
 text TEXT NOT NULL,
 embedding VECTOR({EMBED_DIM}) NOT NULL,
 search_tsv TSVECTOR,
 derived_rule_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
 uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
 )
 """
 )
 op.create_index("ix_rag_source_docs_document_id", "rag_source_docs", ["document_id"])
 op.create_index("ix_rag_source_docs_regulator", "rag_source_docs", ["regulator"])
 op.execute(
 "CREATE INDEX ix_rag_source_docs_derived ON rag_source_docs USING GIN (derived_rule_ids)"
 )
 op.execute("CREATE INDEX ix_rag_source_docs_tsv ON rag_source_docs USING GIN (search_tsv)")
 op.execute(
 "CREATE INDEX ix_rag_source_docs_embedding ON rag_source_docs "
 "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
 )

 # tsvector triggers — auto-populate search_tsv on insert/update
 op.execute(
 """
 CREATE OR REPLACE FUNCTION rag_rules_tsv_trigger() RETURNS trigger AS $$
 BEGIN
 NEW.search_tsv :=
 setweight(to_tsvector('english', COALESCE(NEW.rule_text, '')), 'A') ||
 setweight(to_tsvector('english',
 COALESCE(array_to_string(
 ARRAY(SELECT jsonb_array_elements_text(COALESCE(NEW.keywords, '[]'::jsonb))),
 ' '
 ), '')
 ), 'B') ||
 setweight(to_tsvector('english', COALESCE(NEW.category, '')), 'C');
 RETURN NEW;
 END
 $$ LANGUAGE plpgsql;
 """
 )
 op.execute(
 "CREATE TRIGGER rag_rules_tsv_trg BEFORE INSERT OR UPDATE ON rag_rules "
 "FOR EACH ROW EXECUTE FUNCTION rag_rules_tsv_trigger()"
 )

 op.execute(
 """
 CREATE OR REPLACE FUNCTION rag_chunks_tsv_trigger() RETURNS trigger AS $$
 BEGIN
 NEW.search_tsv := to_tsvector('english', COALESCE(NEW.text, ''));
 RETURN NEW;
 END
 $$ LANGUAGE plpgsql;
 """
 )
 op.execute(
 "CREATE TRIGGER rag_chunks_tsv_trg BEFORE INSERT OR UPDATE ON rag_chunks "
 "FOR EACH ROW EXECUTE FUNCTION rag_chunks_tsv_trigger()"
 )

 op.execute(
 """
 CREATE OR REPLACE FUNCTION rag_source_docs_tsv_trigger() RETURNS trigger AS $$
 BEGIN
 NEW.search_tsv :=
 setweight(to_tsvector('english', COALESCE(NEW.text, '')), 'A') ||
 setweight(to_tsvector('english', COALESCE(NEW.document_title, '')), 'B');
 RETURN NEW;
 END
 $$ LANGUAGE plpgsql;
 """
 )
 op.execute(
 "CREATE TRIGGER rag_source_docs_tsv_trg BEFORE INSERT OR UPDATE ON rag_source_docs "
 "FOR EACH ROW EXECUTE FUNCTION rag_source_docs_tsv_trigger()"
 )


def downgrade() -> None:
 op.execute("DROP TRIGGER IF EXISTS rag_source_docs_tsv_trg ON rag_source_docs")
 op.execute("DROP TRIGGER IF EXISTS rag_chunks_tsv_trg ON rag_chunks")
 op.execute("DROP TRIGGER IF EXISTS rag_rules_tsv_trg ON rag_rules")
 op.execute("DROP FUNCTION IF EXISTS rag_source_docs_tsv_trigger")
 op.execute("DROP FUNCTION IF EXISTS rag_chunks_tsv_trigger")
 op.execute("DROP FUNCTION IF EXISTS rag_rules_tsv_trigger")

 op.drop_table("rag_source_docs")
 op.drop_table("rag_chunks")
 op.drop_table("rag_rules")
 # Don't drop the extension — other tables may use it.
