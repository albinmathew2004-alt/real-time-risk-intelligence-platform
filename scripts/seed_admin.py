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
from app.auth.demo_admin import ensure_demo_admin


def main() -> None:
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        result = ensure_demo_admin(db)
        print(
            f"[OK] Demo admin verified: {result['email']} "
            f"(role={result['role']}, created={result['created']}, updated={result['updated']}, active={result['is_active']})"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
