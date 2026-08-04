"""The change-password endpoint's own rules.

It used to hash whatever `new_password` it was handed: no length, no
complexity, no reuse check. A one-character password was accepted on a system
whose purpose is regulatory control, and the only length rule in the repo lived
in a seed script. These pin the two guards that now sit at the boundary.
"""
import pytest
from pydantic import ValidationError

from app.api.routes.auth import PasswordChangeIn
from app.auth.passwords import MIN_PASSWORD_LEN


def test_a_short_new_password_is_refused_by_the_schema():
    with pytest.raises(ValidationError):
        PasswordChangeIn(current_password="whatever", new_password="x")


def test_a_password_one_short_of_the_minimum_is_still_refused():
    with pytest.raises(ValidationError):
        PasswordChangeIn(current_password="whatever", new_password="a" * (MIN_PASSWORD_LEN - 1))


def test_a_password_at_the_minimum_is_accepted():
    body = PasswordChangeIn(current_password="whatever", new_password="a" * MIN_PASSWORD_LEN)
    assert body.new_password == "a" * MIN_PASSWORD_LEN


def test_the_current_password_is_not_length_checked():
    """Only the NEW password has to satisfy today's rule — an account created
    before the minimum existed must still be able to rotate away from its old
    short password."""
    body = PasswordChangeIn(current_password="x", new_password="a" * MIN_PASSWORD_LEN)
    assert body.current_password == "x"
