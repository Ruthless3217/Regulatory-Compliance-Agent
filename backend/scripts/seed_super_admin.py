"""Seed the initial super-admin account from environment settings.

Idempotent: does nothing if the username already exists or if
SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD are not configured.

Run inside the backend container (schema must already be at head):
    python -m scripts.seed_super_admin
"""
import logging

from app.database import SessionLocal
from app.models.user import User
from app.auth.passwords import hash_password
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def seed() -> None:
    if not settings.super_admin_username or not settings.super_admin_password:
        logger.info("SUPER_ADMIN_USERNAME or SUPER_ADMIN_PASSWORD not set. Skipping seed.")
        return

    db = SessionLocal()
    try:
        existing = (
            db.query(User)
            .filter(User.username == settings.super_admin_username)
            .first()
        )
        if existing:
            logger.info("Super admin %s already exists.", settings.super_admin_username)
            return

        user = User(
            username=settings.super_admin_username,
            password_hash=hash_password(settings.super_admin_password),
            role="super_admin",
            registered_ip=settings.super_admin_ip or "127.0.0.1",
            is_active=True,
            must_change_password=True,
            display_name="Super Admin",
        )
        db.add(user)
        db.commit()
        logger.info("Super admin %s seeded successfully.", settings.super_admin_username)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
