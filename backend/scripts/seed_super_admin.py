"""Bootstrap the first super-admin account (no open 'create first admin' route).

Usage (after `alembic upgrade head`), reading SUPER_ADMIN_* from the env:
    python -m scripts.seed_super_admin

Idempotent: a no-op if any super_admin already exists. Never prints the password.
The account is created with must_change_password=True so the real password is set
by the human on first login.
"""
import os
import sys
from pathlib import Path

# Allow running as a module from the backend dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.config import settings  # noqa: E402
from app.models.user import User  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402


def main() -> None:
    username = settings.super_admin_username.strip()
    password = settings.super_admin_password
    ip = settings.super_admin_ip.strip()

    if not username or not password or not ip:
        print(
            "SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD and SUPER_ADMIN_IP must all be set. "
            "Aborting (no account created)."
        )
        sys.exit(1)

    db = SessionLocal()
    try:
        existing_super = db.query(User).filter(User.role == "super_admin").first()
        if existing_super is not None:
            print(f"A super_admin already exists (username={existing_super.username!r}); no-op.")
            return

        # Guard against a username collision with a non-super_admin row.
        if db.query(User).filter(User.username == username).first() is not None:
            print(f"Username {username!r} is already taken by a non-super_admin user; aborting.")
            sys.exit(1)

        user = User(
            username=username,
            password_hash=hash_password(password),
            registered_ip=ip,
            role="super_admin",
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        db.commit()
        print(f"Created super_admin {username!r} bound to IP {ip} (must change password on first login).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
