from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.demo_attempt_cleanup import expire_stale_demo_attempts  # noqa: E402
from engine.db.database import Base, SessionLocal, current_database_mode, engine, ensure_demo_schema  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark stale demo attempts as EXPIRED without deleting telemetry or evidence.")
    parser.add_argument("--timeout-minutes", type=int, default=None, help="Override the stale demo timeout in minutes")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    ensure_demo_schema()

    db = SessionLocal()
    try:
        result = expire_stale_demo_attempts(db, timeout_minutes=args.timeout_minutes)
        if result["updated_count"]:
            db.commit()
        print(
            json.dumps(
                {
                    "database_mode": current_database_mode(),
                    "timeout_minutes": result["timeout_minutes"],
                    "updated_count": result["updated_count"],
                    "status_applied": result["status"],
                    "message": result["message"],
                    "attempt_ids": result["updated_attempt_ids"],
                },
                indent=2,
            )
        )
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
