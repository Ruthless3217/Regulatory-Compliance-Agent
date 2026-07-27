from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class UserSession(Base):
    """Durable record of a login session for session-time reporting.

    The PK ``id`` is the opaque server session id (``sid``) — the same value held
    in the Redis hot session and in the httpOnly cookie — so Redis, Postgres, and
    the ``session_id`` columns on analysis_runs / llm_usage_events all line up.
    """

    __tablename__ = "user_sessions"

    id = Column(String(64), primary_key=True)  # the sid (secrets.token_urlsafe)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(400), nullable=True)
    login_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    logout_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="active", server_default="active")  # active|closed|expired

    user = relationship("User", back_populates="sessions", foreign_keys=[user_id])
