from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Boolean, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from ..database import Base


class AnalysisRun(Base):
    """One invocation of the compliance graph for a submission.

    A first-class fact table (not derived from compliance_checks) because the
    engine's fail-closed persistability gate persists NO ComplianceCheck for
    degraded/failed runs — yet those runs still burn tokens and must be costed.
    Each re-run is a distinct row with its own actor, timing, tokens, and cost.
    """

    __tablename__ = "analysis_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    session_id = Column(String(64), nullable=True)
    run_number = Column(Integer, nullable=False)  # 1 = first, 2+ = re-run
    is_rerun = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    trigger_source = Column(String(16), nullable=False, default="sync", server_default="sync")  # sync|async|stream
    status = Column(String(20), nullable=False, default="running", server_default="running")  # running|completed|needs_review|failed
    compliance_check_id = Column(
        UUID(as_uuid=True),
        ForeignKey("compliance_checks.id", ondelete="SET NULL"),
        nullable=True,  # NULL on fail-closed runs
    )
    degraded_reason = Column(String(64), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    completion_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_cost_usd = Column(Numeric(12, 6), nullable=False, default=0, server_default=text("0"))
