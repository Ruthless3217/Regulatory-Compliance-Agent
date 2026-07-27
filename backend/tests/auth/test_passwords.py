"""Phase 1 (audit-trail): password hashing primitive (argon2id).

Pure functions — no DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_hash_is_not_plaintext():
    from app.auth.passwords import hash_password

    h = hash_password("s3cret-passw0rd")
    assert isinstance(h, str)
    assert h != "s3cret-passw0rd"
    assert h.startswith("$argon2")


def test_verify_round_trip():
    from app.auth.passwords import hash_password, verify_password

    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_rejects_wrong_password():
    from app.auth.passwords import hash_password, verify_password

    h = hash_password("correct horse battery staple")
    assert verify_password("Correct Horse Battery Staple", h) is False
    assert verify_password("", h) is False


def test_verify_is_safe_on_garbage_hash():
    from app.auth.passwords import verify_password

    # a malformed / empty stored hash must return False, never raise.
    assert verify_password("anything", "") is False
    assert verify_password("anything", "not-a-real-hash") is False


def test_hashes_are_salted():
    from app.auth.passwords import hash_password

    assert hash_password("same-password") != hash_password("same-password")
