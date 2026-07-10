import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Numeric, Index, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class LlmUsageEvent(Base):
    __tablename__ = "llm_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_id = Column(String(64))
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True)
    feature = Column(String(32), nullable=False)
    profile = Column(String(16))
    provider = Column(String(32))
    model_name = Column("model", String(128))  # Use model_name for Python attribute to avoid conflicts, but "model" for DB
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    input_cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    output_cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    total_cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    token_source = Column(String(12), nullable=False, default="measured")
    price_source = Column(String(12), nullable=False, default="configured")
    latency_ms = Column(Integer)
    is_retry = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    user = relationship("User", backref="llm_usage_events")
    submission = relationship("Submission", backref="llm_usage_events")
    run = relationship("AnalysisRun", backref="llm_usage_events")

    __table_args__ = (
        Index("ix_usage_user_created", "user_id", desc("created_at")),
        Index("ix_usage_submission", "submission_id"),
        Index("ix_usage_run", "run_id"),
        Index("ix_usage_model", "model", "created_at"),
        Index("ix_usage_created", "created_at"),
    )
