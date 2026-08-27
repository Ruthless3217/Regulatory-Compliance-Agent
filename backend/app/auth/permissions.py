"""Role -> permission mapping.

Expressed as unions rather than three literal sets, so the hierarchy
user < admin < super_admin is a property of the code instead of something three
lists have to be kept in agreement about. They had already drifted: super_admin
previously held no submission permissions at all, which made the highest role
unable to open a document.

Spec: docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md section 4
"""

_USER = frozenset({
    "submission:create", "submission:read", "submission:delete", "analysis:run",
    "comparison:use", "dashboard:view", "knowledgebase:view",
    "rules:read", "feedback:submit",
    # Ability to act on an assignment. Does NOT grant access to any particular
    # one — every work action also checks assignee_id == caller.
    "assignments:work",
})

_ADMIN = _USER | frozenset({
    "rules:write", "rules:generate", "feedback:review",
    "assignments:manage",   # assign, reassign, cancel, see every bucket
    "trail:view",           # per-document and per-reviewer trail
    "submission:approve",   # split out of submission:create — reviewers lose it
    # Split out of submission:delete for the same reason, and by the same rule.
    # "submission:delete" guards TWO different things: removing one comment,
    # which is ordinary reviewer work, and DELETE /submissions/{id}, which
    # cascades away every revision, check, violation, comment, run and
    # assignment the document ever had. A reviewer being handed a document to
    # review is not a reason to let them destroy it — and moving the existing
    # permission wholesale would have taken comment deletion with it.
    "submission:purge",
})

# Console permissions stay here rather than in _ADMIN: widening admin was not
# asked for. The trail capability admin needs is carried by trail:view.
_SUPER_ADMIN = _ADMIN | frozenset({
    "users:manage",
    "console:view", "audit:view", "usage:view",
})

ROLE_PERMISSIONS = {
    "user": _USER,
    "admin": _ADMIN,
    "super_admin": _SUPER_ADMIN,
}


def role_has(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, frozenset())
