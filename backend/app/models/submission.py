from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class Submission(Base):
 __tablename__ = "submissions"

 id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
 title = Column(String(500), nullable=False)
 content_type = Column(String(50), nullable=False) # html, markdown, pdf, docx, text
 original_content = Column(Text)
 file_path = Column(String(1000))
 submitted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
 submitted_at = Column(DateTime(timezone=True), server_default=func.now())

 # Status field
 # Values: uploaded, preprocessing, preprocessed, analyzing, analyzed, failed
 status = Column(String(50), default="uploaded")

 # Approval status
 # Values: pending, approved, rejected
 approval_status = Column(String(50), default="pending", nullable=False)

 # Product / jurisdiction the content is for — used to scope rule + precedent
 # retrieval so a ULIP submission isn't graded against term-plan-only rules
 # (architect-audit: multi-jurisdiction/product conflation).
 product_line = Column(String(100), nullable=True, index=True)
 jurisdiction = Column(String(100), nullable=True, index=True)

 # Relationships
 compliance_checks = relationship("ComplianceCheck", back_populates="submission", cascade="all, delete-orphan")
 submitter = relationship("User", back_populates="submissions", foreign_keys=[submitted_by])

 # Chunks relationship for granular content processing
 chunks = relationship(
 "ContentChunk",
 back_populates="submission",
 cascade="all, delete-orphan",
 order_by="ContentChunk.chunk_index"
 )
