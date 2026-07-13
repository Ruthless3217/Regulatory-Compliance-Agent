"""Seed grader (user-role) accounts for the compliance review UI.

Idempotent: skips any username that already exists.

Run inside the backend container (schema must already be at head):
    python -m scripts.seed_grader
"""
import logging

from app.database import SessionLocal
from app.models.user import User
from app.auth.passwords import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Add / edit graders here ─────────────────────────────────────────
GRADERS = [
    {
        "username": "grader1",
        "password": "Grader@123",
        "display_name": "Grader One",
        "registered_ip": "0.0.0.0",
    },
]
# ────────────────────────────────────────────────────────────────────


def seed() -> None:
    db = SessionLocal()
    try:
        for g in GRADERS:
            existing = (
                db.query(User)
                .filter(User.username == g["username"])
                .first()
            )
            if existing:
                logger.info("SKIP  %s — already exists (id=%s).", g["username"], existing.id)
                continue

            user = User(
                username=g["username"],
                password_hash=hash_password(g["password"]),
                role="user",
                registered_ip=g.get("registered_ip", "0.0.0.0"),
                is_active=True,
                must_change_password=True,
                display_name=g.get("display_name", g["username"]),
            )
            db.add(user)
            db.commit()
            logger.info("ADDED %s (id=%s, role=user).", g["username"], user.id)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
