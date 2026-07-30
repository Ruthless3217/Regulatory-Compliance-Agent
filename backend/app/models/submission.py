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
    content_type = Column(String(50), nullable=False)  # html, markdown, pdf, docx, text
    original_content = Column(Text)
    # 0025 — the editable working copy. NULL means "no edits yet, current ==
    # original". original_content stays immutable (grading/highlighting
    # reference); current_content is what edits/apply-fix/restore write.
    current_content = Column(Text, nullable=True)
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

    # 0024 — async page-render pass status (processing|completed|failed|
    # skipped), independent of `status` (the analysis lifecycle): a submission
    # can finish analysis while its pages are still rendering.
    page_render_status = Column(String(50), nullable=True)

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

    # 0026/0027 — content-versioning and freestanding comments.
    revisions = relationship(
        "SubmissionRevision",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionRevision.revision_number",
    )
    comments = relationship(
        "DocumentComment",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="DocumentComment.created_at",
    )
