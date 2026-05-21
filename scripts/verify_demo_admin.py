from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.db.database import Base, SessionLocal, engine
from app.auth.demo_admin import resolve_demo_admin_config
from app.models.user import User, UserRole


def main() -> None:
    config = resolve_demo_admin_config()
    if not config.email:
        raise SystemExit(
            "Missing DEMO_ADMIN_EMAIL (or DEFAULT_ADMIN_EMAIL). "
            "Set it before verifying the hosted demo admin."
        )

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == config.email).first()
        if not user:
            raise SystemExit(f"[WARN] Demo admin not found for email: {config.email}")

        role_ok = user.role == UserRole.ADMIN.value
        print(f"[OK] Demo admin exists: {user.email}")
        print(f"[INFO] Role: {user.role}")
        print(f"[INFO] Active: {user.is_active}")
        print(f"[INFO] Name: {user.full_name or '—'}")
        print(f"[INFO] Role is ADMIN: {role_ok}")
        if not role_ok:
            raise SystemExit("[WARN] Demo admin exists but role is not ADMIN.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
