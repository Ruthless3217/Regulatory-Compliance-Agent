from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from ..database import Base


class AgentTrace(Base):
 __tablename__ = "agent_traces"

 id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
 execution_id = Column(UUID(as_uuid=True), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False, index=True)
 step_number = Column(String(50), nullable=True)
 thought = Column(Text, nullable=True)
 action = Column(String(200), nullable=True)
 action_input = Column(JSONB, nullable=True)
 observation = Column(Text, nullable=True)
 created_at = Column(DateTime(timezone=True), server_default=func.now())
