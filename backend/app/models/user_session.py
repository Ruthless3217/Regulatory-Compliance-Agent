import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base

class UserSession(Base):
    __tablename__ = "user_sessions"

    # The primary key is the opaque session token (secrets.token_urlsafe(32)),
    # mirroring the Redis session id so logout/heartbeat can db.get(UserSession, sid).
    id = Column(String(64), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ip = Column(String(64))
    user_agent = Column(String(400))
    login_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True))
    logout_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    status = Column(String(20), nullable=False, default="active", index=True)

    user = relationship("User", back_populates="sessions")

    __table_args__ = (
        Index("ix_user_sessions_user", "user_id", desc("login_at")),
    )
