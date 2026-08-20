"""A unit of review work: one submission handed to one reviewer.

Deliberately a separate table rather than an `assigned_to` column on
`submissions`, so a reassignment keeps the history of who held the document
before (`superseded_by` chains the rows) instead of overwriting it.

The reviewer-done state is `awaiting_signoff`, not `submitted` — in this
codebase "submitted" already means *uploaded* (`submissions.submitted_at`),
and overloading it would make queries ambiguous to read.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 1
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base

# The states in which a submission counts as "already assigned". The partial
# unique index in migration 0041 is defined over exactly this tuple.
ACTIVE_STATUSES = ("open", "in_review", "awaiting_signoff")


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id = Column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT, not SET NULL: an assignment with no assignee is meaningless.
    # This system deactivates users (`is_active`) rather than deleting them,
    # so the constraint should never fire in normal operation.
    assignee_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    status = Column(String(20), nullable=False, default="open", server_default="open")
    priority = Column(String(10), nullable=False, default="normal", server_default="normal")
    due_at = Column(DateTime(timezone=True))
    note = Column(Text)  # the admin's instruction to the reviewer

    assigned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))
    closed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    outcome = Column(String(20))       # approved | rejected | cancelled | superseded
    outcome_note = Column(Text)        # send-back / rejection reason
    superseded_by = Column(
        UUID(as_uuid=True),
        ForeignKey("review_assignments.id", ondelete="SET NULL"),
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    submission = relationship("Submission")
    assignee = relationship("User", foreign_keys=[assignee_id])
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        # The bucket query: "everything open for this reviewer".
        Index("ix_review_assignments_assignee_status", "assignee_id", "status"),
    )
