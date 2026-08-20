import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index, desc
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(48), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_role = Column(String(20))
    actor_ip = Column(String(64))
    session_id = Column(String(64))
    # Denormalized document scope. Events whose target is a violation or a
    # comment still belong to a submission's trail; this makes that trail one
    # indexed lookup instead of a join chain or a JSONB scan (migration 0041).
    scope_submission_id = Column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True
    )
    target_type = Column(String(24))
    target_id = Column(String(64))
    before_state = Column("before", JSONB)
    after_state = Column("after", JSONB)
    metadata_ = Column("metadata", JSONB)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    actor = relationship("User", backref="audit_events")

    __table_args__ = (
        Index("ix_audit_type_created", "event_type", desc("created_at")),
        Index("ix_audit_actor_created", "actor_user_id", desc("created_at")),
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_scope_submission_created", "scope_submission_id", desc("created_at")),
    )
