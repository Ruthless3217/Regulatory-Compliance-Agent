"""Destroying a submission is administrative; deleting a comment is not.

`submission:delete` guarded both, and every role holds it. So a reviewer handed
a document to review could issue DELETE /submissions/{id} and cascade away every
revision, compliance check, violation, comment, analysis run and assignment the
document ever had — the entire evidentiary record of the review, including the
parts that existed to hold *them* accountable. Being assigned a document, having
uploaded it, or being able to edit it are all reasons to work on it, none of
them a reason to be able to destroy it.

The fix is the same split that already took sign-off away from reviewers
(see test_approval_permission.py): a new, narrow permission on _ADMIN, with the
destructive route pointed at it. Moving `submission:delete` wholesale would have
taken comment deletion with it, which is ordinary reviewer work and is
deliberately left alone.
"""
import pathlib
import re

from app.auth.permissions import ROLE_PERMISSIONS, role_has

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
SRC = (ROUTES / "submissions.py").read_text(encoding="utf-8")


def _signature_of(decorator: str) -> str:
    """The parameter list of the route declared by `decorator`."""
    body = SRC[SRC.index(decorator):]
    return body[: body.index("):")]


# --- A / B: reviewers cannot destroy a submission --------------------------

def test_reviewers_cannot_purge():
    # True whether the submission is assigned to them or was uploaded by them:
    # the permission is not held at all, so visibility never comes into it.
    assert not role_has("user", "submission:purge")


def test_admins_and_super_admins_can_purge():
    assert role_has("admin", "submission:purge")
    assert role_has("super_admin", "submission:purge")


def test_the_destructive_route_requires_the_new_permission():
    signature = _signature_of('@router.delete("/{submission_id}")')
    assert 'require("submission:purge")' in signature, (
        "DELETE /submissions/{id} cascades away the whole review record and must "
        "not be reachable with a permission every role holds."
    )


def test_direct_uuid_access_cannot_bypass_it():
    """The guard is the route dependency, not anything the caller supplies.

    `require(...)` runs before the handler body, so there is no submission id —
    guessed, assigned, or owned — that reaches the delete. Asserted at the
    source because that ordering is a property of the decorator, not of any
    particular request.
    """
    signature = _signature_of('@router.delete("/{submission_id}")')
    assert "Depends(require(" in signature
    assert "get_visible_submission" not in signature, (
        "visibility must not be the only thing standing between a reviewer and "
        "a cascading delete — it grants exactly the access being abused."
    )


# --- C / D / I: nothing else moved -----------------------------------------

def test_comment_deletion_stays_ordinary_reviewer_work():
    # The reason a new permission was added instead of moving the old one.
    assert role_has("user", "submission:delete")
    signature = _signature_of('@router.delete("/{submission_id}/comments/{comment_id}")')
    assert 'require("submission:delete")' in signature


def test_the_two_deletes_are_guarded_by_different_permissions():
    purge = _signature_of('@router.delete("/{submission_id}")')
    comment = _signature_of('@router.delete("/{submission_id}/comments/{comment_id}")')
    assert 'require("submission:purge")' in purge
    assert 'require("submission:purge")' not in comment


def test_the_hierarchy_still_nests():
    # user ⊂ admin ⊂ super_admin is the property the module is built on;
    # adding a permission must not break it.
    assert ROLE_PERMISSIONS["user"] < ROLE_PERMISSIONS["admin"]
    assert ROLE_PERMISSIONS["admin"] < ROLE_PERMISSIONS["super_admin"]


def test_purge_is_the_only_permission_this_change_added():
    added = ROLE_PERMISSIONS["admin"] - ROLE_PERMISSIONS["user"]
    assert "submission:purge" in added
    # Pins the blast radius: reviewers keep everything they had.
    for perm in (
        "submission:create", "submission:read", "submission:delete",
        "analysis:run", "comparison:use", "assignments:work",
    ):
        assert role_has("user", perm), f"reviewers lost {perm}"


def test_reviewers_still_cannot_approve_or_manage():
    # Unchanged by this phase; asserted so a permissions edit cannot loosen
    # them unnoticed while this file is the one being reviewed.
    for perm in ("submission:approve", "assignments:manage", "trail:view"):
        assert not role_has("user", perm)


def test_no_other_route_moved_to_the_new_permission():
    """Exactly one route may require it. A second would mean the split grew
    beyond the destructive delete it was written for."""
    assert len(re.findall(r'require\("submission:purge"\)', SRC)) == 1


# --- the UI reflects this, and must keep reflecting it ---------------------

REPO = pathlib.Path(__file__).resolve().parents[2]
FRONTEND_PERMISSIONS = REPO / "frontend" / "lib" / "permissions.ts"


def test_the_frontend_mirror_agrees_about_who_may_purge():
    """The delete button is gated on a client-side copy of this mapping.

    The copy exists because /auth/me returns a role, not a permission list. It
    is only a mirror — the route dependency above is the authorization — but a
    mirror that drifts either shows reviewers a button that 403s (the bug this
    phase fixed) or hides it from the admins who need it. Nothing else notices
    the two files disagreeing, so this does.
    """
    src = FRONTEND_PERMISSIONS.read_text(encoding="utf-8")
    listed = re.search(r'"submission:purge":\s*\[([^\]]*)\]', src)
    assert listed, f"no submission:purge entry in {FRONTEND_PERMISSIONS}"

    mirrored = set(re.findall(r'"([^"]+)"', listed.group(1)))
    backend = {r for r in ROLE_PERMISSIONS if role_has(r, "submission:purge")}
    assert mirrored == backend, (
        f"{FRONTEND_PERMISSIONS.name} says {sorted(mirrored)} may purge; "
        f"permissions.py says {sorted(backend)}"
    )
