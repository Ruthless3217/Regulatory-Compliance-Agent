from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
from ..database import Base


class DocumentComparison(Base):
    __tablename__ = "document_comparisons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    old_content_type = Column(String(50), nullable=False)  # docx, pdf, text
    new_content_type = Column(String(50), nullable=False)
    old_file_path = Column(String(1000), nullable=True)
    new_file_path = Column(String(1000), nullable=True)
    old_original_content = Column(Text, nullable=True)
    new_original_content = Column(Text, nullable=True)
    diff_result = Column(JSONB, nullable=True)
    status = Column(String(50), nullable=False, default="processing")  # processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
