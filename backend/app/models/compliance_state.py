from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ComplianceState(Base):
    """Persisted compliance state for HITL (Human-in-the-Loop) workflows."""
    __tablename__ = "compliance_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(String(100), nullable=False, index=True)
    user_id = Column(String(100), nullable=True)
    status = Column(String(50), default="pending")
    chunks = Column(JSONB, nullable=True)
    active_rules = Column(JSONB, nullable=True)
    violations = Column(JSONB, nullable=True)
    active_agents = Column(JSONB, nullable=True)
    scores = Column(JSONB, nullable=True)
    messages = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
