"""Role hierarchy (spec D5).

super_admin is a strict superset of admin, admin of user. `users:manage` is
super_admin's alone. These are asserted structurally so the sets cannot drift
apart again the way they already had — super_admin previously held no
submission permissions at all.
"""
from app.auth.permissions import ROLE_PERMISSIONS, role_has


def test_hierarchy_is_a_strict_chain():
    user = ROLE_PERMISSIONS["user"]
    admin = ROLE_PERMISSIONS["admin"]
    super_admin = ROLE_PERMISSIONS["super_admin"]

    assert set(user) < set(admin), "admin must be a strict superset of user"
    assert set(admin) < set(super_admin), "super_admin must be a strict superset of admin"


def test_only_super_admin_manages_users():
    assert not role_has("user", "users:manage")
    assert not role_has("admin", "users:manage")
    assert role_has("super_admin", "users:manage")


def test_approval_is_admin_and_above():
    assert not role_has("user", "submission:approve")
    assert role_has("admin", "submission:approve")
    assert role_has("super_admin", "submission:approve")


def test_assignment_permissions():
    # everyone can work their own bucket; only admin+ can hand work out
    for r in ("user", "admin", "super_admin"):
        assert role_has(r, "assignments:work")
    assert not role_has("user", "assignments:manage")
    assert role_has("admin", "assignments:manage")
    assert role_has("super_admin", "assignments:manage")


def test_trail_is_admin_and_above():
    assert not role_has("user", "trail:view")
    assert role_has("admin", "trail:view")
    assert role_has("super_admin", "trail:view")


def test_console_stays_super_admin_only():
    # Deliberate: widening admin was not requested. admin answers
    # "who did what" through trail:view instead.
    for perm in ("console:view", "audit:view", "usage:view"):
        assert not role_has("admin", perm)
        assert role_has("super_admin", perm)


def test_super_admin_can_read_submissions():
    # regression: super_admin previously had no submission permissions
    for perm in ("submission:read", "submission:create", "analysis:run",
                 "comparison:use", "dashboard:view"):
        assert role_has("super_admin", perm)


def test_unknown_role_has_nothing():
    assert not role_has("nope", "submission:read")
    assert not role_has("", "submission:read")
