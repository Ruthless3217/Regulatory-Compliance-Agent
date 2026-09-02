"""The super-admin console's user-provisioning and password-reset schemas.

CreateUserIn.password and UpdateUserIn.new_password used to accept a
password of any length — the MIN_PASSWORD_LEN rule enforced everywhere else
(seed script, the change-password endpoint) was missing here. These pin the
same guard on the admin console's two password-setting paths.
"""
import pytest
from pydantic import ValidationError

from app.api.routes.admin_console import CreateUserIn, UpdateUserIn
from app.auth.passwords import MIN_PASSWORD_LEN


def test_create_user_with_a_short_password_is_refused_by_the_schema():
    with pytest.raises(ValidationError):
        CreateUserIn(username="new_user", password="x")


def test_create_user_password_at_the_minimum_is_accepted():
    body = CreateUserIn(username="new_user", password="a" * MIN_PASSWORD_LEN)
    assert body.password == "a" * MIN_PASSWORD_LEN


def test_update_user_with_a_short_new_password_is_refused_by_the_schema():
    with pytest.raises(ValidationError):
        UpdateUserIn(new_password="x")


def test_update_user_without_a_new_password_is_still_allowed():
    """new_password stays optional — an update that doesn't touch the
    password (role change, deactivation) must not be forced to supply one."""
    body = UpdateUserIn(role="admin")
    assert body.new_password is None
