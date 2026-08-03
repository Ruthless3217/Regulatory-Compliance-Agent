from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from ..database import Base


class SubmissionRevision(Base):
    """A full-content snapshot of a submission's document after an edit —
    the one mutation primitive behind manual edits, apply-fix, bulk-apply-fixes,
    and restore (migration 0026).

    Stores the WHOLE document per revision (not a diff), so restoring or
    diffing two revisions never needs to replay a patch chain.
    `revision_number` is 1-based per submission and addressable directly
    (UNIQUE(submission_id, revision_number)).
    """

    __tablename__ = "submission_revisions"
    __table_args__ = (
        UniqueConstraint("submission_id", "revision_number", name="uq_submission_revisions_submission_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision_number = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # The same edit as a Lexical editor state, plus the HTML the client
    # serialized from it at the same instant. NULL for text-only saves and for
    # every revision predating the editor.
    lexical_state = Column(JSONB, nullable=True)
    lexical_html = Column(Text, nullable=True)
    # manual_edit | apply_fix | bulk_apply_fixes | restore
    source = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    # Findings this revision applied fixes for (apply_fix / bulk_apply_fixes),
    # so the UI can show a violation as already-applied.
    applied_violation_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    submission = relationship("Submission", back_populates="revisions")
    creator = relationship("User")
