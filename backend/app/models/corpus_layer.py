from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func, text
import uuid
from ..database import Base

# The kinds of ingest a layer can describe. Kept as a plain frozenset rather
# than a DB enum so adding a kind is a code change, not a migration.
CORPUS_LAYER_KINDS = frozenset(
    {"precedent_ingest", "reviewer_feedback", "source_docs", "product_docs"}
)


class CorpusLayer(Base):
    """One ingested contribution to the corpus, with provenance and an off switch
    (migration 0030).

    `enabled=False` hides every precedent carrying this layer id from retrieval
    instantly and reversibly — no re-embedding, no data loss. Destroying content
    is a separate, explicit `purge_layer()` call.

    Precedents with a NULL `source_layer_id` predate layers and are always
    retrieved; they belong to no layer and cannot be switched off here.
    """

    __tablename__ = "corpus_layers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    kind = Column(String(30), nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    # Folder, file or batch id the contribution came from. For a
    # `precedent_ingest` layer this matches `precedent_cases.source_file`.
    source_ref = Column(Text, nullable=True)
    ingested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    item_count = Column(Integer, nullable=True, server_default=text("0"), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (Index("ix_corpus_layers_enabled", "enabled"),)
