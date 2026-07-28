import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Numeric, Index, desc
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False)
    triggered_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_id = Column(String(64))
    run_number = Column(Integer, nullable=False)
    is_rerun = Column(Boolean, nullable=False, default=False)
    trigger_source = Column(String(16), nullable=False, default="sync")
    status = Column(String(20), nullable=False, default="running")
    compliance_check_id = Column(UUID(as_uuid=True), ForeignKey("compliance_checks.id", ondelete="SET NULL"), nullable=True)
    degraded_reason = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
    duration_ms = Column(Integer)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    total_cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    # Observability extract of the final graph state: retrieval scope +
    # per-candidate accept/reject debug, grounding mix, degradation flags
    # (migration 0022, RETRIEVAL_RCA.md §4).
    run_metadata = Column(JSONB, nullable=True)

    # Relationships
    submission = relationship("Submission", backref="runs")
    user = relationship("User", backref="analysis_runs")
    compliance_check = relationship("ComplianceCheck", backref="runs")

    __table_args__ = (
        Index("ix_analysis_runs_submission", "submission_id", "run_number"),
        Index("ix_analysis_runs_user", "triggered_by", desc("started_at")),
        Index("ix_analysis_runs_started", desc("started_at")),
    )
