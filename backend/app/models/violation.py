from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Float, Boolean
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

    # Citation locators for rule-grounded findings — the exact clause/section,
    # page, and the regulation version active at decision time. Audit-defensible
    # traceability ("violates Section 41, doc vX, p.Y").
    cited_section = Column(Text, nullable=True)
    cited_page = Column(Integer, nullable=True)
    cited_regulation_version = Column(String(50), nullable=True)

    # Snapshot of the rule's version at the moment this finding was made, so a
    # later rule edit/deactivation can't rewrite history. rule_id may go NULL on
    # rule delete; this integer survives as the version-of-record.
    rule_version = Column(Integer, nullable=True)

    # Sub-confidence-floor / uncertain findings are persisted (NOT dropped) with
    # suppressed=true so they're auditable and routable to a human review lane.
    # Suppressed findings do NOT affect the score. See architect-audit
    # (silent false-negative suppression).
    suppressed = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    suppressed_reason = Column(Text, nullable=True)

    # 0023 — which AnalysisRun produced this finding, and its reviewer-facing
    # lifecycle (open/actioned) independent of the rule_feedback audit trail.
    analysis_run_id = Column(UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    review_status = Column(String(20), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # 0024 — real page/bbox anchor for the document viewer, matching
    # pdf_render_service.PositionedWord ([x0,y0,x1,y1]), replacing the
    # synthetic "chunk:N" string in `location` for PDF-sourced uploads.
    section_title = Column(Text, nullable=True)
    anchor_page = Column(Integer, nullable=True)
    anchor_bbox = Column(JSONB, nullable=True)

    # 0028 — has this finding's suggested_fix already been written into the
    # document via a submission_revisions entry?
    fix_applied = Column(Boolean, nullable=False, default=False, server_default="false")
    fix_applied_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    compliance_check = relationship("ComplianceCheck", back_populates="violations")
    rule = relationship("Rule")
    analysis_run = relationship("AnalysisRun")
