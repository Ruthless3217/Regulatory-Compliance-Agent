from sqlalchemy import Column, Float, DateTime, ForeignKey, Index, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class RuleReliabilityEvent(Base):
    """Append-only before/after snapshot of a rule's Beta-Binomial reliability
    (alpha/beta/theta) written on every reviewer verdict (migration 0029) —
    the audit trail `rules.reliability_alpha/beta` never had, since that pair
    is mutated in place with no history log.

    `rule_feedback_id` is nullable: a future direct/manual adjustment might
    not have an originating rule_feedback row.
    """

    __tablename__ = "rule_reliability_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("rules.id", ondelete="SET NULL"), nullable=True)
    rule_feedback_id = Column(UUID(as_uuid=True), ForeignKey("rule_feedback.id", ondelete="SET NULL"), nullable=True)
    alpha_before = Column(Float, nullable=True)
    beta_before = Column(Float, nullable=True)
    alpha_after = Column(Float, nullable=True)
    beta_after = Column(Float, nullable=True)
    theta_before = Column(Float, nullable=True)
    theta_after = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    rule = relationship("Rule")
    rule_feedback = relationship("RuleFeedback")

    __table_args__ = (
        Index("ix_rule_reliability_events_rule", "rule_id", desc("created_at")),
        Index("ix_rule_reliability_events_feedback", "rule_feedback_id"),
    )
