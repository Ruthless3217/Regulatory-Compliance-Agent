"""Which submissions and comparisons a caller may see.

    admin, super_admin -> everything
    everyone else      -> assigned to me, or uploaded by me   (submissions)
                       -> created by me                       (comparisons)

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
from sqlalchemy import false, or_, select

from app.auth.permissions import role_has
from app.models.document_comparison import DocumentComparison
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


# --- Comparisons -----------------------------------------------------------
# The same three entry points, in the same shapes, for the same reason. A
# comparison holds both documents in full (`old_original_content` /
# `new_original_content`), their uploaded files and their rendered pages, so
# reading someone else's is a content disclosure, not a metadata one.
#
# Ownership is `created_by` and nothing else: `document_comparisons` has no
# submission_id, so there is no assignment to inherit visibility from. Whether
# comparisons should join the submission domain is a data-model question and is
# deliberately NOT decided here.


def may_see_comparison(comparison, user) -> bool:
    """Creator, or a role that sees every bucket.

    No `db` parameter, unlike `may_see_submission`: there is no assignment
    table to consult, and taking one would imply a relationship that does not
    exist.
    """
    if _is_unrestricted(user):
        return True

    uid = getattr(user, "id", None)
    if uid is None:
        return False

    # `created_by` is nullable, and rows predating authentication have it NULL.
    # Those are readable by admins only. Fail closed on purpose: the safe
    # reading of "nobody is recorded as the owner" is not "everybody owns it".
    if comparison.created_by is None:
        return False

    return str(comparison.created_by) == str(uid)


def get_visible_comparison(db, comparison_id, user):
    """Drop-in replacement for the `db.query(DocumentComparison)...first()` +
    404 pair that every comparison route currently opens with.

    404, never 403 — same convention as submissions, and for the same reason:
    a 403 confirms the comparison exists, which is itself a disclosure. It also
    means knowing a comparison's UUID buys an outsider nothing.
    """
    comparison = (
        db.query(DocumentComparison)
        .filter(DocumentComparison.id == comparison_id)
        .first()
    )
    if comparison is None or not may_see_comparison(comparison, user):
        raise HTTPException(status_code=404, detail="Comparison not found")
    return comparison


def visible_comparison_filter(user):
    """A WHERE clause for the list query, or None when the caller is
    unrestricted."""
    if _is_unrestricted(user):
        return None
    uid = getattr(user, "id", None)
    if uid is None:
        # `created_by == None` renders as IS NULL, which would list exactly the
        # unowned rows `may_see_comparison` refuses. Match nothing instead, so
        # the two helpers cannot disagree about the same caller.
        return false()
    return DocumentComparison.created_by == uid
