from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class Violation(Base):
    __tablename__ = "violations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    compliance_check_id = Column(UUID(as_uuid=True), ForeignKey("compliance_checks.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("rules.id", ondelete="SET NULL"), nullable=True, index=True)
    category = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(Text, nullable=True)
    current_text = Column(Text, nullable=True)
    suggested_fix = Column(Text, nullable=True)
    auto_fixable = Column(String(5), default="false")
    chunk_id = Column(UUID(as_uuid=True), nullable=True)
    chunk_index = Column(Integer, nullable=True)
    violation_metadata = Column(JSONB, nullable=True)

    # P1.3 — LLM-reported confidence in [0,1]. Lets the UI bucket low-confidence
    # findings into a "needs human review" lane and weight scoring accordingly.
    confidence = Column(Float, nullable=False, default=0.85, server_default="0.85")

    # P1.3 — Verbatim regulator passage backing this violation. Populated either
    # by the analysis LLM (citation mode) or by the post-hoc citation enforcer
    # via the rag_source_docs lookup. Required for audit-defensibility.
    regulator_quote = Column(Text, nullable=True)

    # Phase 1.5 — precedent-citation columns. Populated when the violation came
    # from the precedent path; let the UI show the historic ticket, the
    # reviewer-marked anchor text, the reviewer's comment verbatim, and the
    # approved final rewrite. Reviewer name is intentionally NOT persisted.
    cited_precedent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    cited_document_id = Column(Text, nullable=True, index=True)
    cited_source_file = Column(Text, nullable=True)
    cited_anchor_text = Column(Text, nullable=True)
    cited_comment_verbatim = Column(Text, nullable=True)
    cited_final_text = Column(Text, nullable=True)
    similarity_score = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    compliance_check = relationship("ComplianceCheck", back_populates="violations")
    rule = relationship("Rule")
