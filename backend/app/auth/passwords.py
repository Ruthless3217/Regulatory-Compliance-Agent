"""Password hashing — Argon2id (memory-hard, modern).

The plaintext password is never stored or logged; only the Argon2 hash is
persisted on ``users.password_hash``. ``verify_password`` is written to *never
raise* — a malformed/empty stored hash simply returns ``False`` — so an
attacker cannot distinguish "no password set" from "wrong password", and a
corrupt row can't 500 the login route.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

# Defaults are the argon2-cffi recommended parameters; tune per 08 if needed.
_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    """Return an Argon2id hash of ``raw`` (salted; two calls differ)."""
    return _ph.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    """True iff ``raw`` matches ``hashed``. Never raises."""
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, raw)
    except Argon2Error:
        # VerifyMismatchError, InvalidHashError, VerificationError, ...
        return False
    except Exception:
        # Defensive: any unexpected argon2 internal error must not leak/raise.
        return False
