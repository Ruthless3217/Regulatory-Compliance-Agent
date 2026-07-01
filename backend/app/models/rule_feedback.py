from sqlalchemy import (
 Column, String, Text, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class RuleFeedback(Base):
 """One reviewer verdict on one finding — the audit trail behind every
 rule-weight update (adaptive rule weights / HITL).

 A verdict is upserted per (violation, reviewer): re-submitting flips the
 stored verdict and the service reverts the old pseudo-count before
 applying the new one, so the net effect is always exactly one verdict.
 rule_id is denormalized from the violation at write time so the weight
 trail survives even if the violation's rule link is severed later.
 """

 __tablename__ = "rule_feedback"
 __table_args__ = (
 UniqueConstraint("violation_id", "reviewer_id", name="uq_rule_feedback_violation_reviewer"),
 )

 id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
 violation_id = Column(
 UUID(as_uuid=True),
 ForeignKey("violations.id", ondelete="CASCADE"),
 nullable=False,
 index=True,
 )
 rule_id = Column(
 UUID(as_uuid=True),
 ForeignKey("rules.id", ondelete="SET NULL"),
 nullable=True,
 index=True,
 )
 verdict = Column(String(10), nullable=False) # accept | reject
 # Reviewer's severity re-grade (evidence for human-approved severity
 # migration — never applied automatically).
 severity_override = Column(String(20), nullable=True)
 reviewer_id = Column(
 UUID(as_uuid=True),
 ForeignKey("users.id", ondelete="SET NULL"),
 nullable=True,
 index=True,
 )
 comment = Column(Text, nullable=True)
 created_at = Column(DateTime(timezone=True), server_default=func.now())
 updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

 violation = relationship("Violation")
 rule = relationship("Rule")
