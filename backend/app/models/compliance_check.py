from sqlalchemy import Column, String, Text, DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    overall_score = Column(Float, nullable=True)
    grade = Column(String(10), nullable=True)  # A, B, C, D, F
    status = Column(String(50), default="completed")

    # Score breakdown by category
    scores = Column(JSONB, nullable=True)

    # Relationships
    submission = relationship("Submission", back_populates="compliance_checks")
    violations = relationship("Violation", back_populates="compliance_check", cascade="all, delete-orphan")
