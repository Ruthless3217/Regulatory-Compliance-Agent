"""Review-assignment lifecycle.

    open --> in_review --> awaiting_signoff --> closed
      |          ^               |
      |          '-- send back --'
      '--> superseded (reassignment) / cancelled

Every state change goes through `_transition`, which consults one explicit edge
map. The alternative — `if status ==` checks spread across routes — is how a
workflow ends up with states nobody can enumerate.

Uniqueness of the active assignment is enforced twice on purpose: here, so the
API returns a clean 409, and by the partial unique index in migration 0041, so
two concurrent admins cannot both win the check-then-insert.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 2
"""
import uuid
from datetime import datetime, timezone

from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.services.observability import audit

# `closed` is reachable from every ACTIVE state, not just `awaiting_signoff`.
# Sign-off is a terminal authority: an admin approving a document ends the work
# wherever the reviewer had got to. Restricting it to `awaiting_signoff` left an
# approved document holding a live assignment forever, which drained no bucket
# and — because active rows are what the partial unique index watches — kept the
# document un-assignable. Terminal states stay terminal.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_review", "closed", "superseded", "cancelled"},
    "in_review": {"awaiting_signoff", "closed", "superseded", "cancelled"},
    "awaiting_signoff": {"in_review", "closed", "superseded", "cancelled"},
    "closed": set(),
    "superseded": set(),
    "cancelled": set(),
}

VALID_PRIORITIES = ("low", "normal", "high", "urgent")
VALID_OUTCOMES = ("approved", "rejected", "cancelled", "superseded")


class AssignmentError(Exception):
    """Base for lifecycle refusals. Routes map these to 4xx."""


class ActiveAssignmentExists(AssignmentError):
    """This submission already has an open/in_review/awaiting_signoff row."""


class IllegalTransition(AssignmentError):
    """The requested state change is not an edge in ALLOWED_TRANSITIONS."""


class NotAssignee(AssignmentError):
    """A work action was attempted by someone who does not hold the assignment."""


def _now():
    return datetime.now(timezone.utc)


def active_for_submission(db, submission_id):
    """The one active assignment for this submission, or None.

    Queried per-status rather than with IN(...) because the test double
    supports only equality filters — and because the (assignee_id, status)
    index makes each lookup trivial anyway.
    """
    for status in ACTIVE_STATUSES:
        row = (
            db.query(ReviewAssignment)
            .filter(
                ReviewAssignment.submission_id == submission_id,
                ReviewAssignment.status == status,
            )
            .first()
        )
        if row is not None:
            return row
    return None


def _require_assignee(assignment, actor):
    if str(getattr(actor, "id", None)) != str(assignment.assignee_id):
        raise NotAssignee("This assignment belongs to someone else.")


def _transition(assignment, to_status):
    allowed = ALLOWED_TRANSITIONS.get(assignment.status, set())
    if to_status not in allowed:
        raise IllegalTransition(
            f"Cannot move an assignment from {assignment.status!r} to {to_status!r}."
        )
    assignment.status = to_status


def _new_assignment(**kwargs):
    """The model's `default=uuid.uuid4` is applied by the INSERT, not by the
    constructor, so `.id` would be None until flush. Reassignment needs the id
    immediately (to chain `superseded_by`), so it is assigned up front."""
    kwargs.setdefault("id", uuid.uuid4())
    return ReviewAssignment(**kwargs)


def assign(db, *, submission_id, assignee_id, actor, priority="normal",
           due_at=None, note=None):
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Unknown priority {priority!r}.")
    if active_for_submission(db, submission_id) is not None:
        raise ActiveAssignmentExists(
            "This document is already assigned. Reassign it instead."
        )

    assignment = _new_assignment(
        submission_id=submission_id,
        assignee_id=assignee_id,
        assigned_by=getattr(actor, "id", None),
        status="open",
        priority=priority,
        due_at=due_at,
        note=note,
        assigned_at=_now(),
    )
    db.add(assignment)

    audit.record_sync(
        db, "assignment_created", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=submission_id,
        after={"assignee_id": str(assignee_id), "status": "open",
               "priority": priority, "due_at": due_at.isoformat() if due_at else None},
        metadata={"note": note},
    )
    return assignment


def reassign(db, *, assignment, new_assignee_id, actor, note=None):
    """Close the current row and open a fresh one for the new reviewer.

    Order matters: the old row is superseded BEFORE the new one is added, so
    the partial unique index never observes two active rows for the submission.
    """
    previous_assignee = assignment.assignee_id
    _transition(assignment, "superseded")
    assignment.outcome = "superseded"
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)

    replacement = _new_assignment(
        submission_id=assignment.submission_id,
        assignee_id=new_assignee_id,
        assigned_by=getattr(actor, "id", None),
        status="open",
        priority=assignment.priority,
        due_at=assignment.due_at,
        note=note if note is not None else assignment.note,
        assigned_at=_now(),
    )
    db.add(replacement)
    assignment.superseded_by = replacement.id

    audit.record_sync(
        db, "assignment_reassigned", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        before={"assignee_id": str(previous_assignee), "status": "open"},
        after={"assignee_id": str(new_assignee_id),
               "assignment_id": str(replacement.id)},
    )
    return replacement


def start(db, *, assignment, actor):
    _require_assignee(assignment, actor)
    _transition(assignment, "in_review")
    assignment.started_at = _now()
    audit.record_sync(
        db, "assignment_started", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "in_review"},
    )
    return assignment


def complete(db, *, assignment, actor):
    _require_assignee(assignment, actor)
    _transition(assignment, "awaiting_signoff")
    assignment.completed_at = _now()
    audit.record_sync(
        db, "assignment_completed", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "awaiting_signoff"},
    )
    return assignment


def send_back(db, *, assignment, actor, reason):
    """Return work to the reviewer who did it. Ownership is unchanged — this is
    not a reassignment, so no new row."""
    if not (reason or "").strip():
        raise ValueError("A send-back needs a reason.")
    _transition(assignment, "in_review")
    assignment.completed_at = None
    assignment.outcome_note = reason.strip()
    audit.record_sync(
        db, "assignment_sent_back", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        before={"status": "awaiting_signoff"},
        after={"status": "in_review"},
        metadata={"reason": reason.strip()},
    )
    return assignment


def close(db, *, assignment, actor, outcome, note=None):
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Unknown outcome {outcome!r}.")
    _transition(assignment, "closed")
    assignment.outcome = outcome
    assignment.outcome_note = note
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)
    audit.record_sync(
        db, "assignment_closed", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "closed", "outcome": outcome},
        metadata={"note": note},
    )
    return assignment


def cancel(db, *, assignment, actor, reason=None):
    _transition(assignment, "cancelled")
    assignment.outcome = "cancelled"
    assignment.outcome_note = reason
    assignment.closed_at = _now()
    assignment.closed_by = getattr(actor, "id", None)
    audit.record_sync(
        db, "assignment_cancelled", actor=actor,
        target_type="assignment", target_id=str(assignment.id),
        scope_submission_id=assignment.submission_id,
        after={"status": "cancelled"},
        metadata={"reason": reason},
    )
    return assignment
