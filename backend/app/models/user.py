from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(500), unique=True, nullable=False, index=True)
    display_name = Column(String(200), nullable=True)
    firebase_uid = Column(String(200), unique=True, nullable=True, index=True)
    role = Column(String(50), default="user")  # user, admin, super_admin
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    submissions = relationship("Submission", back_populates="submitter", foreign_keys="Submission.submitted_by")
    created_rules = relationship("Rule", back_populates="creator", foreign_keys="Rule.created_by")
