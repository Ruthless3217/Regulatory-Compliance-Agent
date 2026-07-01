from sqlalchemy import Column, String, Text, DateTime, Boolean, Numeric, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class Rule(Base):
 __tablename__ = "rules"

 id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
 category = Column(String(20), nullable=False, index=True) # regulatory, brand, seo
 rule_text = Column(Text, nullable=False)
 severity = Column(String(20), nullable=False, index=True) # critical, high, medium, low
 keywords = Column(JSONB) # Array of keywords
 pattern = Column(String(1000)) # Optional regex
 is_active = Column(Boolean, default=True)
 created_at = Column(DateTime(timezone=True), server_default=func.now())
 rule_metadata = Column(JSONB, nullable=True)

 # --- Versioning (audit-defensibility) ---
 # Rules are versioned, not mutated in place. An edit creates a NEW row with
 # version = old.version + 1 and points the old row's superseded_by at the new
 # one (and deactivates the old). effective_date is when the rule takes force.
 # Past violations snapshot rule_version so decisions re-trace to the exact
 # version active at decision time. See architect-audit (no rule versioning).
 version = Column(Integer, nullable=False, default=1, server_default="1")
 effective_date = Column(DateTime(timezone=True), nullable=True)
 superseded_by = Column(
 UUID(as_uuid=True), ForeignKey("rules.id", ondelete="SET NULL"), nullable=True, index=True
 )

 # --- Scoping (multi-product / multi-jurisdiction isolation) ---
 # Stops a product-specific rule (e.g. ULIP min-death-benefit) from being
 # applied to an unrelated product (the 0008 mis-sourced-rules problem).
 product_line = Column(String(100), nullable=True, index=True)
 jurisdiction = Column(String(100), nullable=True, index=True)

 # Dynamic rule generation fields
 points_deduction = Column(
 Numeric(5, 2),
 nullable=False,
 default=-5.00,
 comment="Point deduction value for compliance score calculation"
 )
 created_by = Column(
 UUID(as_uuid=True),
 ForeignKey('users.id', ondelete='SET NULL'),
 nullable=True,
 index=True
 )

 # --- Adaptive rule weights (Beta-Binomial reliability) ---
 # Pseudo-counts behind θ = α/(α+β), the learned probability that a finding
 # fired by this rule is correct. Updated only by reviewer accept/reject
 # verdicts (rule_feedback_service); NULL = no feedback history → scoring
 # applies full penalty exactly as before. See reliability.py.
 reliability_alpha = Column(Numeric(10, 2), nullable=True)
 reliability_beta = Column(Numeric(10, 2), nullable=True)

 # Auto-generation tracking
 is_auto_generated = Column(Boolean, default=False, nullable=False, index=True)
 generated_from_industry = Column(String(100), nullable=True, index=True)
 generation_source = Column(Text, nullable=True)
 confidence_score = Column(Numeric(3, 2), nullable=True)

 # Relationships
 creator = relationship("User", back_populates="created_rules", foreign_keys=[created_by])
