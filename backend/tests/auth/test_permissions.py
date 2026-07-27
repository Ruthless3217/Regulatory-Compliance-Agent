"""RBAC permission catalogue — role → permission grants (audit-trail 01 §2.2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_user_can_grade_but_not_mutate_rules_or_console():
    from app.auth.permissions import role_has

    assert role_has("user", "analysis:run")
    assert role_has("user", "chat:use")
    assert role_has("user", "rules:read")
    assert not role_has("user", "rules:write")
    assert not role_has("user", "rules:generate")
    assert not role_has("user", "console:view")
    assert not role_has("user", "usage:view")
    assert not role_has("user", "users:manage")


def test_admin_adds_rule_authority_and_user_management():
    from app.auth.permissions import role_has

    # everything a user can do
    assert role_has("admin", "analysis:run")
    assert role_has("admin", "chat:use")
    # plus rule authority + provisioning
    assert role_has("admin", "rules:write")
    assert role_has("admin", "rules:generate")
    assert role_has("admin", "users:manage")
    # but NOT the super-admin console
    assert not role_has("admin", "console:view")
    assert not role_has("admin", "audit:view")


def test_super_admin_is_console_only_and_cannot_grade():
    from app.auth.permissions import role_has

    # console + governance
    assert role_has("super_admin", "console:view")
    assert role_has("super_admin", "usage:view")
    assert role_has("super_admin", "audit:view")
    assert role_has("super_admin", "users:manage")
    assert role_has("super_admin", "rules:write")
    # explicitly CANNOT grade documents / use chat / compare
    assert not role_has("super_admin", "analysis:run")
    assert not role_has("super_admin", "chat:use")
    assert not role_has("super_admin", "comparison:use")
    assert not role_has("super_admin", "submission:create")
    assert not role_has("super_admin", "dashboard:view")


def test_unknown_role_has_nothing():
    from app.auth.permissions import role_has

    assert not role_has("root", "rules:read")
    assert not role_has("", "rules:read")
    assert not role_has(None, "rules:read")
