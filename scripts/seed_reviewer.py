from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.db.database import Base, SessionLocal, engine
from app.auth.security import hash_password
from app.models.user import User, UserRole


def main() -> None:
    email = os.getenv("DEFAULT_REVIEWER_EMAIL", "")
    password = os.getenv("DEFAULT_REVIEWER_PASSWORD", "")
    full_name = os.getenv("DEFAULT_REVIEWER_NAME", "Default Reviewer")

    if not email or not password:
        raise SystemExit(
            "Missing DEFAULT_REVIEWER_EMAIL/DEFAULT_REVIEWER_PASSWORD. "
            "Set them as environment variables before running this script."
        )

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"[OK] Reviewer already exists: {existing.email} (role={existing.role}, active={existing.is_active})")
            return

        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=UserRole.REVIEWER.value,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"[OK] Created reviewer user: {user.email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
