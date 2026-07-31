"""Create or promote an admin-capable account from environment variables.

The counterpart to scripts/seed_super_admin.py, which only ever creates a
``super_admin`` and skips entirely once that username exists. This one covers
the ``admin`` role (the one holding users:manage / rules:write / feedback:review
and the whole reviewer surface) and, unlike seed_super_admin, PROMOTES an
existing account instead of silently doing nothing.

Idempotent by design — safe to run on every deploy:
  * user missing  -> created from ADMIN_USERNAME/ADMIN_PASSWORD, must_change_password=True
  * user present  -> password is NEVER touched; the role is set to ADMIN_ROLE and
                     nothing else is written. ADMIN_ROLE is the source of truth,
                     so it can also DEMOTE — note that super_admin is not a
                     superset of admin (it has console/audit/usage but no
                     submission:read / dashboard:view — see auth/permissions.py).
  * A deliberately deactivated account is NOT reactivated (that would undo a
    lockout on every deploy) — it is reported loudly instead.

Run inside the backend container, after `alembic upgrade head`:

    ADMIN_USERNAME=compliance_admin \
    ADMIN_PASSWORD='<from your secret store>' \
    python -m scripts.seed_admin

Env:
  ADMIN_USERNAME      required. The login name.
  ADMIN_PASSWORD      required only when the user does not exist yet (>= 12 chars).
  ADMIN_ROLE          admin (default) | super_admin. Must exist in ROLE_PERMISSIONS.
  ADMIN_IP            registered_ip. Default 0.0.0.0 = wildcard, any device
                      (see auth/dependencies.py ip_allowed). Set a real IP only
                      when AUTH_IP_BINDING_MODE is strict.
  ADMIN_DISPLAY_NAME  optional UI label.

Exit codes: 0 ok / 2 misconfigured (missing or too-short password, unknown role).
"""
import logging
import os
import sys

from app.auth.passwords import hash_password
from app.auth.permissions import ROLE_PERMISSIONS
from app.database import SessionLocal
from app.models.user import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_PASSWORD_LEN = 12


def seed() -> int:
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    role = os.environ.get("ADMIN_ROLE", "admin").strip() or "admin"

    if not username:
        logger.error("ADMIN_USERNAME is not set -- nothing to do.")
        return 2
    if role not in ROLE_PERMISSIONS:
        logger.error("ADMIN_ROLE=%r is not a known role (%s).", role, ", ".join(sorted(ROLE_PERMISSIONS)))
        return 2

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()

        if existing:
            if password:
                logger.info("ADMIN_PASSWORD ignored -- %s already exists; re-running never "
                            "resets a password. Use the app's change-password flow.", username)
            if not existing.is_active:
                logger.warning("%s exists but is DEACTIVATED. Not reactivating automatically — "
                               "flip users.is_active yourself if that was not deliberate.", username)
            if existing.role != role:
                logger.warning("Changing role of %s: %s -> %s.", username, existing.role, role)
                existing.role = role
                db.commit()
            else:
                logger.info("%s already has role %s (id=%s). Nothing to do.", username, role, existing.id)
            return 0

        if len(password) < MIN_PASSWORD_LEN:
            logger.error("ADMIN_PASSWORD must be set and at least %d characters to create %s.",
                         MIN_PASSWORD_LEN, username)
            return 2

        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            registered_ip=os.environ.get("ADMIN_IP") or "0.0.0.0",
            is_active=True,
            must_change_password=True,
            display_name=os.environ.get("ADMIN_DISPLAY_NAME") or username,
        )
        db.add(user)
        db.commit()
        logger.info("Created %s (id=%s, role=%s). Must change password at first login.",
                    username, user.id, role)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(seed())
