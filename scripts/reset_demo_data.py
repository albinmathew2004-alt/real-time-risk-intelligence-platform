from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import ATTEMPT_LOG_FILE  # noqa: E402
from engine.cache.redis_client import clear_all_attempt_state  # noqa: E402
from engine.db.database import Base, SessionLocal, engine, ensure_demo_schema  # noqa: E402
from engine.db.models import AttemptLog, InvestigationCase, RawExamEvent, ReviewerAction, RiskHistory  # noqa: E402


def main() -> int:
    Base.metadata.create_all(bind=engine)
    ensure_demo_schema()
    cleared_attempt_state = clear_all_attempt_state()

    db = SessionLocal()
    try:
        db.query(ReviewerAction).delete()
        db.query(InvestigationCase).delete()
        db.query(RiskHistory).delete()
        db.query(AttemptLog).delete()
        db.query(RawExamEvent).delete()
        db.commit()
    finally:
        db.close()

    ATTEMPT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ATTEMPT_LOG_FILE.write_text("", encoding="utf-8")
    print(f"Demo data reset complete. Cleared {cleared_attempt_state} live attempt cache keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
