from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from ..database import Base


class AuditEvent(Base):
    """Append-only record of who did what, when, from where — security (logins),
    money (runs), and governance (rule/user changes) events.

    The application only ever INSERTs here; UPDATE/DELETE are blocked by a DB
    trigger (see migration 0016) and, in prod, a least-privilege DB role.
    """

    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(48), nullable=False)
    actor_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_role = Column(String(20), nullable=True)
    actor_ip = Column(String(64), nullable=True)
    session_id = Column(String(64), nullable=True)
    target_type = Column(String(24), nullable=True)  # rule|user|submission|session|run|null
    target_id = Column(String(64), nullable=True)
    before = Column(JSONB, nullable=True)
    after = Column(JSONB, nullable=True)
    # DB column is "metadata"; the python attribute is renamed because SQLAlchemy
    # declarative reserves the ``metadata`` attribute name (Base.metadata).
    event_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
