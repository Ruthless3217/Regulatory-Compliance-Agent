from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# The one password rule, so the seed script, the change-password endpoint and
# the form in front of it cannot drift apart. It previously lived only in
# scripts/seed_admin.py, which meant the seeded admin had a minimum and every
# password set afterwards through the API had none at all.
MIN_PASSWORD_LEN = 12

_ph = PasswordHasher()

def hash_password(raw: str) -> str:
    return _ph.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False
