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
 grade = Column(String(10), nullable=True) # A, B, C, D, F
 status = Column(String(50), default="completed")

 # Score breakdown by category
 scores = Column(JSONB, nullable=True)

 # --- Held-out evaluation (adaptive rule weights) ---
 # The human reviewer's own document-level score. NEVER an input to scoring
 # or to weight updates — training on the evaluation metric would Goodhart
 # it. Its only use is the convergence curve |overall_score − reviewer_score|
 # that proves (or disproves) the system improves over time.
 reviewer_score = Column(Float, nullable=True)
 reviewer_scored_at = Column(DateTime(timezone=True), nullable=True)

 # Relationships
 submission = relationship("Submission", back_populates="compliance_checks")
 violations = relationship("Violation", back_populates="compliance_check", cascade="all, delete-orphan")
