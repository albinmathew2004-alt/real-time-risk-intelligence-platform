from __future__ import annotations

import os

import sys
from pathlib import Path

# Allow running this script directly from Windows PowerShell even if the
# current working directory isn't the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime_env import load_local_env
from engine.db.database import Base, engine, SessionLocal, current_database_mode, DATABASE_URL
from app.auth.demo_admin import ensure_demo_admin


load_local_env()


def main() -> None:
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        result = ensure_demo_admin(db)
        database_target = "sqlite-local" if str(DATABASE_URL).startswith("sqlite:") else "postgresql"
        print(f"[OK] Database backend: {current_database_mode()} ({database_target})")
        print(
            f"[OK] Demo admin verified: {result['email']} "
            f"(role={result['role']}, created={result['created']}, updated={result['updated']}, active={result['is_active']})"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
