from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Boolean, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from ..database import Base


class LlmUsageEvent(Base):
    """One billed LLM call: input/output tokens + computed USD cost, tagged with
    the full attribution chain (user -> session -> submission -> run -> call).

    Highest-volume new table (a single analysis of a long document is dozens of
    rows). Every billed attempt — including failover retries and schema-correction
    retries — writes its own event, so cost reflects reality.
    """

    __tablename__ = "llm_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )  # NULL = system/ingestion
    session_id = Column(String(64), nullable=True)
    submission_id = Column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True
    )
    run_id = Column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    feature = Column(String(32), nullable=False)  # analysis|chat|quote|rewrite|rule_generation|ingestion|embedding
    profile = Column(String(16), nullable=True)   # main|critic|chat|embedding
    provider = Column(String(32), nullable=True)
    model = Column(String(128), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    completion_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    total_tokens = Column(Integer, nullable=False, default=0, server_default=text("0"))
    input_cost_usd = Column(Numeric(12, 6), nullable=False, default=0, server_default=text("0"))
    output_cost_usd = Column(Numeric(12, 6), nullable=False, default=0, server_default=text("0"))
    total_cost_usd = Column(Numeric(12, 6), nullable=False, default=0, server_default=text("0"))
    token_source = Column(String(12), nullable=False, default="measured", server_default="measured")  # measured|estimated
    price_source = Column(String(12), nullable=False, default="configured", server_default="configured")  # configured|default
    latency_ms = Column(Integer, nullable=True)
    is_retry = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
