from __future__ import annotations

import os

import sys
from pathlib import Path

# Allow running this script directly from Windows PowerShell even if the
# current working directory isn't the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.db.database import Base, engine, SessionLocal
from app.auth.security import hash_password
from app.models.user import User, UserRole


def main() -> None:
    email = os.getenv("DEFAULT_ADMIN_EMAIL", "")
    password = os.getenv("DEFAULT_ADMIN_PASSWORD", "")
    full_name = os.getenv("DEFAULT_ADMIN_NAME", "Default Admin")

    if not email or not password:
        raise SystemExit(
            "Missing DEFAULT_ADMIN_EMAIL/DEFAULT_ADMIN_PASSWORD. "
            "Set them as environment variables before running this script."
        )

    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"[OK] Admin already exists: {existing.email} (role={existing.role}, active={existing.is_active})")
            return

        user = User(
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"[OK] Created admin user: {user.email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
