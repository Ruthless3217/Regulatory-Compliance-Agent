"""RBAC permission catalogue (audit-trail 01 §2.2).

Roles are NOT a numeric hierarchy — super_admin is *not* a superset of admin,
because super_admin must not grade documents. So each role maps to an explicit
set of named permissions, checked per-endpoint via ``require(permission)``.

Note on user management: ``users:manage`` is granted to both admin and
super_admin; the "admin may create only ``user`` accounts" rule (Decision D3) is
enforced at the handler level (guard the target role), not by a separate
permission string.
"""
from __future__ import annotations

# Base grants for a document grader.
_USER = {
    "submission:create",
    "submission:read",
    "submission:delete",
    "analysis:run",       # incl. re-run, sync/async/stream
    "chat:use",           # chat / quote / rewrite
    "comparison:use",
    "dashboard:view",
    "knowledgebase:view",
    "rules:read",
    "feedback:submit",
}

# Admin = user + rule authority + KB ingest + user provisioning.
_ADMIN = _USER | {
    "rules:write",
    "rules:generate",
    "knowledgebase:ingest",
    "users:manage",
}

# Super-admin = console + governance; NO grading (no analysis/chat/comparison/
# submission/dashboard).
_SUPER_ADMIN = {
    "knowledgebase:view",
    "rules:read",
    "rules:write",
    "rules:generate",
    "feedback:submit",
    "console:view",
    "users:manage",
    "audit:view",
    "usage:view",
    "knowledgebase:ingest",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "user": _USER,
    "admin": _ADMIN,
    "super_admin": _SUPER_ADMIN,
}


def role_has(role: str | None, permission: str) -> bool:
    """True iff ``role`` is granted ``permission``. Unknown/blank role → False."""
    if not role:
        return False
    return permission in ROLE_PERMISSIONS.get(role, set())
