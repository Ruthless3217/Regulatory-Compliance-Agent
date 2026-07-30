from sqlalchemy import Column, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class DocumentComment(Base):
    """A reviewer's freestanding note anchored to a text selection in a
    submission's document (migration 0027) — mirrors `ComparisonAnnotation`'s
    shape but for a single document rather than a two-side comparison.

    Anchors to `anchor_text` (the selected substring) rather than a stable
    change id, since a submission's document has no comparison-style
    per-change id. `page_number` is nullable: plain-text documents have no
    pages.
    """

    __tablename__ = "document_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    anchor_text = Column(Text, nullable=True)
    page_number = Column(Integer, nullable=True)
    body = Column(Text, nullable=False)
    resolved = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    submission = relationship("Submission", back_populates="comments")
    creator = relationship("User")
