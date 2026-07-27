from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(500), unique=True, nullable=True, index=True)
    display_name = Column(String(200), nullable=True)
    # NOTE: firebase_uid is intentionally NOT mapped — the codebase is dropping
    # it (a parallel migration removes the column). Login is username-based, so
    # the column is unused; if it still exists in the DB it is simply ignored.
    role = Column(String(50), default="user")  # user, admin, super_admin
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- Auth columns (audit-trail plan, migration 0014) ---------------------
    # login handle (unique via partial index ix_users_username WHERE NOT NULL)
    username = Column(String(150), nullable=True)
    password_hash = Column(String(255), nullable=True)          # argon2id; never logged
    registered_ip = Column(String(64), nullable=True)           # bound IP (strict mode)
    allowed_ips = Column(JSONB, nullable=True)                  # optional (list mode)
    allowed_cidr = Column(String(64), nullable=True)           # optional (cidr mode)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    must_change_password = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    password_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    submissions = relationship("Submission", back_populates="submitter", foreign_keys="Submission.submitted_by")
    created_rules = relationship("Rule", back_populates="creator", foreign_keys="Rule.created_by")
    sessions = relationship(
        "UserSession", back_populates="user", foreign_keys="UserSession.user_id"
    )
