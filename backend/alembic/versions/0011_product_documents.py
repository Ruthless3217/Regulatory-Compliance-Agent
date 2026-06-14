"""Brochure Phase 1 — product document reference corpus.

  product_documents:  registry of ingested brochures (UIN, descriptor,
                      file-hash idempotency, fail-closed quarantine status)
  product_tables:     structured tables (benefit illustrations, premium
                      charts) as exact JSON rows — never text-chunked
  rag_product_docs:   5th vector index (pgvector + tsvector), the embeddable
                      face of brochures: section chunks with heading-path
                      context + deterministic table summaries

Reference corpus only: these ground compliance checks (product lineage,
violation transfer, pre-flight checklists); they are never graded.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-12
"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBED_DIM = int(os.getenv("RAG_EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    op.create_table(
        "product_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("variant", sa.String(length=200), nullable=True),
        sa.Column("product_type", sa.String(length=50), nullable=True),
        sa.Column("uin", sa.String(length=20), nullable=True),
        sa.Column("uins", postgresql.JSONB(), nullable=True),
        sa.Column("descriptor", sa.Text(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("section_count", sa.Integer(), nullable=True),
        sa.Column("table_count", sa.Integer(), nullable=True),
        sa.Column("body_font_size", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ingested"),
        sa.Column("quarantine_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_product_documents_product_name", "product_documents", ["product_name"])
    op.create_index("ix_product_documents_product_type", "product_documents", ["product_type"])
    op.create_index("ix_product_documents_uin", "product_documents", ["uin"])

    op.create_table(
        "product_tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_product_tables_product_document_id", "product_tables", ["product_document_id"]
    )

    op.execute(
        f"""
        CREATE TABLE rag_product_docs (
            id UUID PRIMARY KEY,
            product_document_id UUID NOT NULL,
            uin VARCHAR(20),
            product_name TEXT,
            chunk_index INTEGER NOT NULL,
            page_number INTEGER,
            section_path TEXT,
            block_type VARCHAR(20) NOT NULL DEFAULT 'prose',
            text TEXT NOT NULL,
            embedding VECTOR({EMBED_DIM}) NOT NULL,
            search_tsv TSVECTOR,
            embedding_model TEXT,
            embedding_dim INTEGER,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.create_index(
        "ix_rag_product_docs_document_id", "rag_product_docs", ["product_document_id"]
    )
    op.create_index("ix_rag_product_docs_uin", "rag_product_docs", ["uin"])
    op.create_index("ix_rag_product_docs_block_type", "rag_product_docs", ["block_type"])
    op.execute("CREATE INDEX ix_rag_product_docs_tsv ON rag_product_docs USING GIN (search_tsv)")
    op.execute(
        "CREATE INDEX ix_rag_product_docs_embedding ON rag_product_docs "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION rag_product_docs_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', COALESCE(NEW.text, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(NEW.section_path, '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(NEW.product_name, '')), 'C');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER rag_product_docs_tsv_trg BEFORE INSERT OR UPDATE ON rag_product_docs "
        "FOR EACH ROW EXECUTE FUNCTION rag_product_docs_tsv_trigger()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS rag_product_docs_tsv_trg ON rag_product_docs")
    op.execute("DROP FUNCTION IF EXISTS rag_product_docs_tsv_trigger")
    op.execute("DROP TABLE IF EXISTS rag_product_docs")

    op.drop_index("ix_product_tables_product_document_id", table_name="product_tables")
    op.drop_table("product_tables")

    op.drop_index("ix_product_documents_uin", table_name="product_documents")
    op.drop_index("ix_product_documents_product_type", table_name="product_documents")
    op.drop_index("ix_product_documents_product_name", table_name="product_documents")
    op.drop_table("product_documents")
