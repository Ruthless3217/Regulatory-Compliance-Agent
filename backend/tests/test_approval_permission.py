"""Sign-off is admin-and-above (spec D4, two-person integrity).

/approve was guarded by `submission:create`, which every role holds — so the
reviewer who edited a document could also sign it off. It now needs
`submission:approve`.

Also pins that the "D3" in-handler role checks in admin_console are gone: once
only super_admin holds users:manage they are unreachable, and unreachable
authorization code reads as if it still protects something.
"""
import pathlib
import re

from app.auth.permissions import role_has

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"


def test_reviewers_cannot_approve():
    assert not role_has("user", "submission:approve")


def test_admins_and_super_admins_can_approve():
    assert role_has("admin", "submission:approve")
    assert role_has("super_admin", "submission:approve")


def test_approve_route_requires_the_approve_permission():
    src = (ROUTES / "submissions.py").read_text(encoding="utf-8")
    approve = src[src.index('@router.post("/{submission_id}/approve")'):]
    signature = approve[: approve.index("):")]
    assert 'require("submission:approve")' in signature, (
        "/approve must require submission:approve, not submission:create"
    )


def test_dead_d3_checks_are_removed():
    src = (ROUTES / "admin_console.py").read_text(encoding="utf-8")
    leftovers = re.findall(r'role", None\) == "admin"', src)
    assert leftovers == [], (
        "Admin no longer holds users:manage, so these branches cannot run. "
        "Unreachable authorization code is worse than none — it reads as a guard."
    )
