from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(500), unique=True, nullable=True, index=True) # nullable=True to allow for future SSO per design doc
    display_name = Column(String(200), nullable=True)
    role = Column(String(50), default="user")  # user, admin, super_admin
    
    # New auth columns
    username = Column(String(150), unique=True, index=True, nullable=True) # UNIQUE INDEX in migration for WHERE username IS NOT NULL
    password_hash = Column(String(255))
    registered_ip = Column(String(64))
    allowed_ips = Column(JSONB)
    allowed_cidr = Column(String(64))
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    last_login_at = Column(DateTime(timezone=True))
    password_updated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    submissions = relationship("Submission", back_populates="submitter", foreign_keys="Submission.submitted_by")
    created_rules = relationship("Rule", back_populates="creator", foreign_keys="Rule.created_by")
    creator = relationship("User", remote_side=[id], backref="created_users")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
