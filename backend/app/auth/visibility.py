"""Which submissions a caller may see.

    admin, super_admin -> everything
    everyone else      -> assigned to me, or uploaded by me

Two entry points on purpose. `get_visible_submission` resolves ONE row and uses
only equality filters, so it works against the suite's in-memory Session double
and reads the same as the `db.query(...).first()` line it replaces.
`visible_submission_filter` builds the set-based clause for list queries, which
needs `or_`/`in_` and is exercised against a real database only.

A caller who may not see a submission gets 404, never 403: a 403 confirms the
document exists, which is itself a disclosure here.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 3
"""
from fastapi import HTTPException
from sqlalchemy import or_, select

from app.auth.permissions import role_has
from app.models.review_assignment import ReviewAssignment
from app.models.submission import Submission

# Holding this permission means "sees every bucket", which is also exactly the
# set of roles that may see every submission.
_UNRESTRICTED = "assignments:manage"


def _is_unrestricted(user) -> bool:
    return role_has(getattr(user, "role", "") or "", _UNRESTRICTED)


def may_see_submission(db, submission, user) -> bool:
    if _is_unrestricted(user):
        return True

    uid = getattr(user, "id", None)
    if uid is None:
        return False

    if submission.submitted_by is not None and str(submission.submitted_by) == str(uid):
        return True

    # Any assignment, not just an active one: a reviewer's own history must not
    # disappear the moment an admin signs the document off.
    assignment = (
        db.query(ReviewAssignment)
        .filter(
            ReviewAssignment.submission_id == submission.id,
            ReviewAssignment.assignee_id == uid,
        )
        .first()
    )
    return assignment is not None


def get_visible_submission(db, submission_id, user):
    """Drop-in replacement for the `db.query(Submission)...first()` + 404 pair
    that every submission-scoped route currently opens with."""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if submission is None or not may_see_submission(db, submission, user):
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


def visible_submission_filter(user):
    """A WHERE clause for list queries, or None when the caller is unrestricted."""
    if _is_unrestricted(user):
        return None
    uid = getattr(user, "id", None)
    return or_(
        Submission.submitted_by == uid,
        Submission.id.in_(
            select(ReviewAssignment.submission_id).where(
                ReviewAssignment.assignee_id == uid
            )
        ),
    )
