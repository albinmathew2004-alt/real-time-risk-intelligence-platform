from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from behavior_profiles import profile_by_name  # noqa: E402
from app.cases.routes import _build_review_queue_payload  # noqa: E402
from app.main import SessionLocal, _build_attempt_report, _build_dashboard_summary  # noqa: E402
from engine.db.models import RiskHistory  # noqa: E402
from engine.core.risk_engine import score_event_batch  # noqa: E402
from simulate_live_exam import DEMO_PROFILES  # noqa: E402


def build_scored_events(candidate: dict, *, seed: int, question_count: int = 8) -> tuple[list[dict], float]:
    random.seed(seed)
    profile = profile_by_name(candidate["profile"])
    question_ids = [f"q{i+1}" for i in range(int(candidate.get("question_count") or question_count))]
    behavior = profile.generate(question_ids=question_ids)
    base = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)

    def make_event(event_type: str, payload: dict, offset_s: float) -> dict:
        return {
            "attempt_id": candidate["attempt_id"],
            "candidate_id": candidate["attempt_id"],
            "candidate_name": candidate["candidate_name"],
            "candidate_email": candidate["candidate_email"],
            "assessment_id": "assessment_python_01",
            "assessment_name": "Python Coding Assessment",
            "event_type": event_type,
            "payload": payload,
            "occurred_at": (base + timedelta(seconds=float(offset_s))).isoformat(),
        }

    events = [make_event("exam_started", {}, 0.0)]
    events.extend(
        make_event(ev.event_type, ev.payload, ev.offset_s)
        for ev in sorted(behavior, key=lambda item: (float(item.offset_s), str(item.event_type)))
    )
    last_offset = max(float(event.offset_s) for event in behavior) if behavior else 60.0
    events.append(make_event("exam_submitted", {}, last_offset + 20.0))
    return events, last_offset + 20.0


def main() -> int:
    base_seed = 42
    validated_attempt_ids: list[str] = []
    for index, candidate in enumerate(DEMO_PROFILES, start=1):
        events, duration_seconds = build_scored_events(candidate, seed=base_seed + index)
        result = score_event_batch(events, attempt_id=candidate["attempt_id"])
        validated_attempt_ids.append(candidate["attempt_id"])
        print(
            {
                "attempt_id": candidate["attempt_id"],
                "candidate_name": candidate["candidate_name"],
                "profile": candidate["profile"],
                "event_count": len(events),
                "duration_seconds": round(duration_seconds, 2),
                "risk": result.risk,
                "base_score": round(result.base_score, 4),
                "combined_score": round(result.combined_score, 4),
                "confidence": round(result.confidence_score, 4),
                "signals": {key: round(float(value.get("score", 0.0) or 0.0), 4) for key, value in (result.signals or {}).items()},
                "pattern_count": len(result.patterns or []),
                "narrative": (result.session_intelligence or {}).get("session_narrative"),
                "explanation": result.explanation_text,
            }
        )

    db = SessionLocal()
    try:
        summary = _build_dashboard_summary(db, recent_hours=24)
        queue = _build_review_queue_payload(
            db,
            status_filter=None,
            risk_level_filter=None,
            assigned_to_filter=None,
            search=None,
            recent_hours=24,
        )
        for attempt_id in validated_attempt_ids:
            report = _build_attempt_report(db, attempt_id)
            dashboard_case = next(
                (row for row in summary.get("cases_needing_review", []) if row.get("attempt_id") == attempt_id),
                None,
            )
            queue_case = next(
                (row for row in queue.get("data", []) if row.get("attempt_id") == attempt_id),
                None,
            )
            print(
                {
                    "consistency_attempt_id": attempt_id,
                    "dashboard_risk": (dashboard_case or {}).get("risk_level"),
                    "dashboard_score": (dashboard_case or {}).get("score"),
                    "queue_risk": (queue_case or {}).get("risk_level"),
                    "queue_score": (queue_case or {}).get("risk_score"),
                    "queue_confidence": (queue_case or {}).get("confidence"),
                    "report_risk": report.get("risk_level"),
                    "report_score": report.get("risk_score"),
                    "report_confidence": report.get("confidence"),
                    "report_reason": report.get("strongest_reason"),
                }
            )
            history_points = (
                db.query(RiskHistory)
                .filter(RiskHistory.attempt_id == attempt_id)
                .order_by(RiskHistory.timestamp.asc(), RiskHistory.id.asc())
                .all()
            )
            print(
                {
                    "risk_history_attempt_id": attempt_id,
                    "snapshot_count": len(history_points),
                    "snapshots": [
                        {
                            "timestamp": row.timestamp,
                            "risk": row.risk,
                            "score": round(float(row.combined_score or 0.0), 4),
                            "reason": row.reason,
                        }
                        for row in history_points[:8]
                    ],
                }
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
