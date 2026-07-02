from sqlalchemy import (
    Column, String, Text, DateTime, ForeignKey, Integer, Float,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ProductDocument(Base):
    """One ingested product brochure/document — the registry row behind the
    rag_product_docs reference corpus (Brochure Phase 1).

    These are NOT submissions: they ground compliance checks (product lineage,
    violation transfer, pre-flight checklists) and never flow through grading.
    file_hash is unique so re-running the batch ingest is idempotent. Documents
    failing parse validation are persisted with status='quarantined' and the
    reasons recorded — fail-closed ingestion, nothing silently skipped.
    """

    __tablename__ = "product_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_name = Column(String(200), nullable=False, index=True)
    variant = Column(String(200), nullable=True)
    product_type = Column(String(50), nullable=True, index=True)  # term/ulip/...
    # Primary (plan) UIN; `uins` keeps every UIN found incl. riders.
    uin = Column(String(20), nullable=True, index=True)
    uins = Column(JSONB, nullable=True)
    # IRDAI-mandated verbatim product descriptor line from page 1.
    descriptor = Column(Text, nullable=True)
    source_file = Column(Text, nullable=False)
    file_hash = Column(String(64), nullable=False, unique=True)
    page_count = Column(Integer, nullable=True)
    section_count = Column(Integer, nullable=True)
    table_count = Column(Integer, nullable=True)
    body_font_size = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="ingested")  # ingested|quarantined
    quarantine_reasons = Column(JSONB, nullable=True)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

    tables = relationship(
        "ProductTable", back_populates="document", cascade="all, delete-orphan"
    )


class ProductTable(Base):
    """Structured table extracted from a brochure (benefit illustrations,
    premium charts, frequency factors). Rows are exact JSON — tables are never
    text-chunked, so numeric lookups stay lossless. The deterministic summary
    is what gets embedded in rag_product_docs (block_type='table_summary')."""

    __tablename__ = "product_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number = Column(Integer, nullable=False)
    table_index = Column(Integer, nullable=False, default=0)
    heading = Column(Text, nullable=True)
    rows = Column(JSONB, nullable=False)
    summary = Column(Text, nullable=True)

    document = relationship("ProductDocument", back_populates="tables")
