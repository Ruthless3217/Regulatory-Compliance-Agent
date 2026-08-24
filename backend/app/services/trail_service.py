"""Read-only assembly of the reviewer trail.

Two sources, joined rather than duplicated into a third table:

* `audit_events` — what someone did, and when. Scoped per document by
  `scope_submission_id` (migration 0041), so one indexed query gets a whole
  document's history including events whose target is a violation or comment.
* `submission_revisions` — the document text at each edit. An edit event
  carries `metadata.revision_number`, which is enough to fetch revisions N and
  N-1 and show a real before/after.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 7
"""
from app.models.audit_event import AuditEvent
from app.models.review_assignment import ACTIVE_STATUSES, ReviewAssignment
from app.models.submission_revision import SubmissionRevision

EDIT_EVENTS = ("submission_edited",)


def _revision_content(db, submission_id, number):
    """The text of one revision, or "" when there isn't one.

    Revision 0 does not exist — an edit against it is the document's first, so
    the "before" side is legitimately empty. A number that names a purged or
    never-written revision also yields "" rather than raising: a broken diff
    must not take the whole trail down with it.
    """
    if number is None or number < 1:
        return ""
    row = (
        db.query(SubmissionRevision)
        .filter(
            SubmissionRevision.submission_id == submission_id,
            SubmissionRevision.revision_number == number,
        )
        .first()
    )
    return (row.content or "") if row is not None else ""


def _diff_for(db, submission_id, event):
    """before/after text for an edit event, or None when it is not an edit.

    Events predating this feature carry no revision_number; they render without
    a diff rather than raising.
    """
    if event.event_type not in EDIT_EVENTS:
        return None
    meta = event.metadata_ or {}
    number = meta.get("revision_number")
    if number is None:
        return None
    return {
        "revision_number": number,
        "before": _revision_content(db, submission_id, number - 1),
        "after": _revision_content(db, submission_id, number),
    }


def _row(db, submission_id, event):
    return {
        "id": str(event.id),
        "at": event.created_at.isoformat() if event.created_at else None,
        "event_type": event.event_type,
        "actor_id": event.actor_user_id,
        "actor_role": event.actor_role,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "metadata": event.metadata_ or {},
        "diff": _diff_for(db, submission_id, event) if submission_id else None,
    }


def _newest_first(events):
    # Sorted in Python rather than SQL so the in-memory test double behaves the
    # same as Postgres; the row count per document is small either way.
    return sorted(events, key=lambda e: e.created_at, reverse=True)


def document_trail(db, submission_id):
    """Everything that happened to one document, newest first."""
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.scope_submission_id == submission_id)
        .all()
    )
    return [_row(db, submission_id, e) for e in _newest_first(events)]


def reviewer_trail(db, user_id, limit: int = 100):
    """Everything one reviewer did, plus their workload counts."""
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.actor_user_id == str(user_id))
        .all()
    )
    activity = [
        _row(db, e.scope_submission_id, e) for e in _newest_first(events)[:limit]
    ]

    assignments = (
        db.query(ReviewAssignment)
        .filter(ReviewAssignment.assignee_id == user_id)
        .all()
    )
    open_count = sum(1 for a in assignments if a.status in ACTIVE_STATUSES)
    closed_count = sum(1 for a in assignments if a.status == "closed")

    return {
        "activity": activity,
        "stats": {
            "open": open_count,
            "closed": closed_count,
            "total": len(assignments),
        },
    }
