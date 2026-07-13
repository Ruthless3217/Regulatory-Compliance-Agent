"""Seed grader (user-role) accounts for the compliance review UI.

Idempotent: skips any username that already exists.

Run inside the backend container (schema must already be at head):
    python -m scripts.seed_grader --ip 10.x.x.x

The --ip flag sets registered_ip for ALL graders in this batch.
AUTH_IP_BINDING_MODE=strict (the default) requires an exact IP match
at login, so pass the client IP users will connect from.
"""
import argparse
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
    },
    {
        "username": "grader2",
        "password": "Grader@456",
        "display_name": "Grader Two",
    },
]
# ────────────────────────────────────────────────────────────────────


def seed(ip: str) -> None:
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
                registered_ip=ip,
                is_active=True,
                must_change_password=True,
                display_name=g.get("display_name", g["username"]),
            )
            db.add(user)
            db.commit()
            logger.info("ADDED %s (id=%s, role=user, ip=%s).", g["username"], user.id, ip)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed grader accounts.")
    parser.add_argument("--ip", default="0.0.0.0", help="registered_ip for all graders (must match client IP when AUTH_IP_BINDING_MODE=strict)")
    args = parser.parse_args()
    seed(args.ip)
