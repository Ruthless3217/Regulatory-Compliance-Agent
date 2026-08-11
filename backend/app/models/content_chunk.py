from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class ContentChunk(Base):
    __tablename__ = "content_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=True)
    chunk_metadata = Column(JSONB, nullable=True)  # page_number, section, etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 0035 — analysis reuse keys (services/agents/compliance/analysis_cache.py).
    # content_hash: sha256 of `text` exactly as stored. Written at chunking time;
    # it is what tells preprocessing whether the document actually changed, and
    # which rows may keep their id (that identity is what violations.chunk_id and
    # the cache hang off).
    # context_key: sha256 of the run fingerprint + this chunk's rendered prompt
    # context, stamped ONLY when a run persists its findings. Equal key + same
    # row = last run's verdicts still stand, so the chunk skips its LLM passes.
    # Both nullable: every pre-0035 chunk has neither and simply never hits.
    content_hash = Column(String(64), nullable=True)
    context_key = Column(String(64), nullable=True)

    # Relationships
    submission = relationship("Submission", back_populates="chunks")

    __table_args__ = (
        Index("ix_content_chunks_submission_hash", "submission_id", "content_hash"),
    )
