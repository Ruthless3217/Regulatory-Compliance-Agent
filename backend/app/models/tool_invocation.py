from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ToolInvocation(Base):
 __tablename__ = "tool_invocations"

 id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
 execution_id = Column(UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False, index=True)
 tool_name = Column(String(100), nullable=False)
 input_data = Column(JSONB, nullable=True)
 output_data = Column(JSONB, nullable=True)
 tokens_used = Column(Integer, default=0)
 latency_ms = Column(Integer, nullable=True)
 created_at = Column(DateTime(timezone=True), server_default=func.now())
