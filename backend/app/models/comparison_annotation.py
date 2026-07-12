from sqlalchemy import Column, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ComparisonAnnotation(Base):
    """A reviewer's note and/or tags attached to one change in a comparison.

    Keyed by (comparison_id, change_id) — change_id is the viewer's selection id
    ("r{n}" for pixel changes, "b{index}" for text-diff blocks). Change ids are
    not stable across a re-run, so a re-run clears all annotations for the row.
    """

    __tablename__ = "comparison_annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comparison_id = Column(
        UUID(as_uuid=True),
        ForeignKey("document_comparisons.id", ondelete="CASCADE"),
        nullable=False,
    )
    change_id = Column(String(50), nullable=False)
    note = Column(Text, nullable=True)
    tags = Column(ARRAY(String), nullable=False, server_default="{}")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("comparison_id", "change_id", name="uq_comparison_annotation_change"),
    )
