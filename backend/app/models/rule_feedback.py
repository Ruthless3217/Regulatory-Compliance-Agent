from sqlalchemy import (
    Column, String, Text, DateTime, Float, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
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
    # 0023 widened accept/reject (String(10)) to the richer reviewer-action
    # taxonomy: accept | reject | correct | not_violation | dismiss.
    verdict = Column(String(20), nullable=False)
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

    # --- 0023: reviewer-action taxonomy snapshot ---
    # Why the reviewer chose this verdict (e.g. "duplicate", "wrong_severity",
    # "out_of_scope") — free-form-enough for a review-queue UI, not an enum.
    reason = Column(String(40), nullable=True)
    # Text snapshots at review time: what the analyzer flagged, what it
    # suggested, and what the reviewer actually approved as final.
    original_text = Column(Text, nullable=True)
    suggested_text = Column(Text, nullable=True)
    final_text = Column(Text, nullable=True)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True, index=True)
    analysis_run_id = Column(UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    # The finding's own confidence, and the retrieval/product context that
    # produced it, snapshotted so later KB/rule edits can't rewrite history.
    confidence_snapshot = Column(Float, nullable=True)
    retrieval_snapshot = Column(JSONB, nullable=True)
    product_snapshot = Column(JSONB, nullable=True)
    model_version = Column(String(100), nullable=True)
    kb_version = Column(String(100), nullable=True)
    # Review-queue routing (e.g. "needs_severity_review", "needs_legal_review")
    # and its resolution, for the admin-only reviewer-actions queues endpoint.
    routed_queue = Column(String(30), nullable=True, index=True)
    queue_resolved_at = Column(DateTime(timezone=True), nullable=True)
    queue_resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    queue_resolved_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    violation = relationship("Violation")
    rule = relationship("Rule")
    submission = relationship("Submission")
    analysis_run = relationship("AnalysisRun")
