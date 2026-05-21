from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import traceback

from engine.core.scorer import run_scoring
from sqlalchemy import text

from engine.db.database import Base, engine, SessionLocal, current_database_mode, ensure_demo_schema
from engine.db.models import AttemptLog, InvestigationCase, RawExamEvent, RiskHistory

from engine.cache.redis_client import (
    append_event,
    get_events,
    redis_health,
    set_current_risk,
    get_current_risk,
)

from app.websocket_manager import manager

# Auth (Phase 1)
from app.auth.routes import router as auth_router
from app.auth.demo_admin import ensure_demo_admin, should_verify_demo_admin_on_startup
from app.auth.dependencies import require_reviewer
from app.cases.routes import router as cases_router
from app.cases.routes import _build_review_queue_payload, _sync_cases_from_attempts
from app.models.user import User  # noqa: F401 (ensures users table is registered on startup)
from app.services.evidence_service import (
    build_violation_overview_counts,
    normalize_evidence,
    normalize_risk_level,
    safe_float,
)
from app.services.final_assessment_service import build_final_risk_assessment
from app.services.report_summary_service import build_report_summary

RISK_HISTORY_SCORE_EPSILON = 0.01
RISK_HISTORY_CONFIDENCE_EPSILON = 0.05
RISK_HISTORY_MIN_GAP_SECONDS = 45
ATTEMPT_LOG_FILE = Path("logs/attempt_logs.jsonl")
APP_VERSION = "2.2.0"
APP_MODE = os.getenv("APP_MODE", "local-demo").strip() or "local-demo"
ACTIONABLE_CASE_STATUSES = {"NEW", "TRIAGED", "UNDER_INVESTIGATION", "ESCALATED"}
COMPLETED_CASE_STATUSES = {"CONFIRMED_RISK", "FALSE_POSITIVE", "CLEARED", "RESOLVED", "CLOSED", "COMPLETED"}


def _is_local_mode() -> bool:
    mode = APP_MODE.strip().lower()
    return mode in {"local", "local-demo", "local-postgres-redis", "dev", "development", "docker-compose"}


def _docs_enabled() -> bool:
    raw = os.getenv("ENABLE_DOCS", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return _is_local_mode()


def _validate_runtime_configuration() -> None:
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY must be set before starting the backend.")
    if not _is_local_mode() and secret.lower() in {"change-me", "demo-secret-key", "secret", "changeme"}:
        raise RuntimeError("JWT_SECRET_KEY must be a strong non-default value when APP_MODE is not local/dev.")


def _allowed_origins() -> list[str]:
    defaults = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]
    configured = os.getenv("CORS_ALLOW_ORIGINS", "")
    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    extra = [origin.strip() for origin in configured.split(",") if origin.strip()]
    if frontend_url:
        extra.append(frontend_url)
    seen = set()
    merged = []
    candidate_origins = (defaults + extra) if _is_local_mode() else extra
    if not _is_local_mode() and any(origin == "*" for origin in candidate_origins):
        raise RuntimeError("CORS_ALLOW_ORIGINS cannot include '*' when APP_MODE is not local/dev.")
    for origin in candidate_origins:
        if origin not in seen:
            seen.add(origin)
            merged.append(origin)
    return merged


app = FastAPI(
    title="Real-Time Risk Intelligence Platform",
    version=APP_VERSION,
    docs_url="/docs" if _docs_enabled() else None,
    redoc_url="/redoc" if _docs_enabled() else None,
    openapi_url="/openapi.json" if _docs_enabled() else None,
)

app.include_router(auth_router)
app.include_router(cases_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    try:
        _validate_runtime_configuration()
        Base.metadata.create_all(bind=engine)
        ensure_demo_schema()
        print("[OK] Database tables created/verified")
        if should_verify_demo_admin_on_startup(APP_MODE):
            db = SessionLocal()
            try:
                ensure_demo_admin(db)
                print("[OK] Demo admin verified")
            finally:
                db.close()
    except Exception as e:
        print("[WARN] Database initialization failed:", e)


def _database_health() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {
            "reachable": True,
            "mode": current_database_mode(),
        }
    except Exception as exc:
        return {
            "reachable": False,
            "mode": current_database_mode(),
            "error": str(exc),
        }
    finally:
        db.close()


class EventBatch(BaseModel):
    attempt_id: str = Field(..., min_length=1, max_length=255)
    events: List[Dict[str, Any]]


class ExamEventIngest(BaseModel):
    attempt_id: str = Field(..., min_length=1, max_length=255)

    candidate_id: Optional[str] = Field(default=None, max_length=255)
    candidate_name: Optional[str] = Field(default=None, max_length=255)
    candidate_email: Optional[str] = Field(default=None, max_length=320)

    assessment_id: Optional[str] = Field(default=None, max_length=255)
    assessment_name: Optional[str] = Field(default=None, max_length=255)

    event_type: str = Field(..., min_length=1, max_length=120)
    payload: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[str] = None

    @field_validator(
        "attempt_id",
        "candidate_id",
        "candidate_name",
        "candidate_email",
        "assessment_id",
        "assessment_name",
        "event_type",
        mode="before",
    )
    @classmethod
    def _strip_string_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _normalize_occurred_at(cls, value: Optional[str]) -> str:
        return normalize_timestamp_input(value, fallback_to_now=True)

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("payload must be a JSON object")
        return value

    @model_validator(mode="after")
    def _validate_privacy_preserving_typing_payload(self):
        if str(self.event_type or "").lower() not in {
            "typing_started",
            "typing_stopped",
            "typing_pause",
            "typing_burst",
            "backspace_activity",
        }:
            return self

        forbidden_keys = {
            "text",
            "value",
            "answer",
            "answer_text",
            "key",
            "keys",
            "character",
            "characters",
            "password",
            "content",
            "raw_input",
        }
        payload_keys = {str(key).lower() for key in (self.payload or {}).keys()}
        leaked = sorted(payload_keys & forbidden_keys)
        if leaked:
            raise ValueError(
                "Typing telemetry must be metadata-only and cannot include content fields: "
                + ", ".join(leaked)
            )
        return self


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def normalize_timestamp_input(value: Any, *, fallback_to_now: bool = False) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return utc_now() if fallback_to_now else ""
    return parsed.isoformat()


def _recent_cutoff(recent_hours: Optional[int]) -> Optional[datetime]:
    if recent_hours is None or recent_hours <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=recent_hours)


def _is_recent_timestamp(value: Any, cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return True
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    return parsed >= cutoff


def build_reason(result: Dict[str, Any]) -> str:
    risk = result.get("risk", "LOW")
    explanation = result.get("explanation", "")
    session_narrative = ((result.get("session_intelligence") or {}).get("session_narrative") or "").strip()

    if isinstance(explanation, str) and explanation.strip():
        return explanation[:500]
    if session_narrative:
        return session_narrative[:500]

    if risk == "HIGH":
        return "Risk escalated to HIGH after repeated deterministic integrity signals accumulated over the session."

    if risk == "MEDIUM":
        return "Risk escalated to MEDIUM after multiple reviewer-relevant irregularities were observed."

    return "Risk remains LOW with stable engagement and limited suspicious behavior."


def _last_timeline_point(result: Dict[str, Any]) -> Dict[str, Any]:
    points = result.get("timeline_points") or []
    if not points:
        return {}
    return points[-1] or {}


def db_events_to_scoring_events(events: List[RawExamEvent]) -> List[Dict[str, Any]]:
    return [
        {
            "event_type": e.event_type,
            "payload": e.payload or {},
            "occurred_at": e.occurred_at,
        }
        for e in events
    ]


def _risk_history_changed(previous: Optional[RiskHistory], *, risk_level: str, score: float, confidence: float, reason: str) -> bool:
    if previous is None:
        return True
    previous_score = safe_float(previous.combined_score, 0.0)
    previous_confidence = safe_float(previous.confidence, 0.0)
    previous_risk = str(previous.risk or normalize_risk_level(previous_score))
    previous_reason = str(previous.reason or "")

    if previous_risk != risk_level:
        return True
    if abs(previous_score - score) >= RISK_HISTORY_SCORE_EPSILON:
        return True
    if abs(previous_confidence - confidence) >= RISK_HISTORY_CONFIDENCE_EPSILON:
        return True
    return False


def _risk_history_gap_elapsed(previous: Optional[RiskHistory], *, timestamp: str) -> bool:
    if previous is None:
        return True
    previous_ts = parse_timestamp(previous.timestamp)
    current_ts = parse_timestamp(timestamp)
    if previous_ts is None or current_ts is None:
        return False
    return (current_ts - previous_ts).total_seconds() >= RISK_HISTORY_MIN_GAP_SECONDS


def _record_risk_snapshot_if_needed(
    db,
    *,
    attempt_id: str,
    candidate_id: Optional[str],
    candidate_name: Optional[str],
    candidate_email: Optional[str],
    assessment_id: Optional[str],
    assessment_name: Optional[str],
    risk_level: str,
    confidence: float,
    score: float,
    reason: str,
    timestamp: str,
    force: bool = False,
) -> Optional[RiskHistory]:
    latest_history = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .first()
    )
    if not force and not _risk_history_changed(
        latest_history,
        risk_level=risk_level,
        score=score,
        confidence=confidence,
        reason=reason,
    ):
        return None

    entry = RiskHistory(
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        assessment_id=assessment_id,
        assessment_name=assessment_name,
        risk=risk_level,
        confidence=confidence,
        combined_score=score,
        reason=reason,
        timestamp=timestamp,
    )
    db.add(entry)
    return entry


def _latest_risk_history_entry(db, attempt_id: str) -> Optional[RiskHistory]:
    return (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .first()
    )


def _persist_timeline_snapshots(
    db,
    *,
    attempt_id: str,
    candidate_id: Optional[str],
    candidate_name: Optional[str],
    candidate_email: Optional[str],
    assessment_id: Optional[str],
    assessment_name: Optional[str],
    timeline_points: List[Dict[str, Any]],
    final_confidence: float,
) -> int:
    if not timeline_points:
        return 0

    existing = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.asc(), RiskHistory.id.asc())
        .all()
    )
    latest_entry = existing[-1] if existing else None
    persisted = 0

    for point in sorted(
        [point for point in timeline_points if point.get("timestamp")],
        key=lambda item: parse_timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
    ):
        timestamp = str(point.get("timestamp"))
        score = safe_float(point.get("score"), 0.0)
        confidence = safe_float(point.get("confidence"), final_confidence)
        risk_level = str(point.get("risk_level") or normalize_risk_level(score))
        reason = str(point.get("trigger") or point.get("reason") or "Behavioral milestone detected").strip()

        if latest_entry is not None:
            latest_ts = parse_timestamp(latest_entry.timestamp)
            point_ts = parse_timestamp(timestamp)
            if latest_ts is not None and point_ts is not None and point_ts <= latest_ts:
                continue

            if not (
                _risk_history_changed(
                    latest_entry,
                    risk_level=risk_level,
                    score=score,
                    confidence=confidence,
                    reason=reason,
                )
                or _risk_history_gap_elapsed(latest_entry, timestamp=timestamp)
            ):
                continue

        force_record = _risk_history_gap_elapsed(latest_entry, timestamp=timestamp)
        latest_entry = _record_risk_snapshot_if_needed(
            db,
            attempt_id=attempt_id,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            assessment_id=assessment_id,
            assessment_name=assessment_name,
            risk_level=risk_level,
            confidence=confidence,
            score=score,
            reason=reason,
            timestamp=timestamp,
            force=force_record,
        ) or latest_entry
        if latest_entry is not None:
            persisted += 1

    return persisted


def _load_attempt_logs() -> List[Dict[str, Any]]:
    if not ATTEMPT_LOG_FILE.exists():
        return []

    rows: List[Dict[str, Any]] = []
    try:
        with ATTEMPT_LOG_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _dedupe_latest_attempt_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_attempt: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        attempt_id = row.get("attempt_id")
        if not attempt_id:
            continue
        previous = latest_by_attempt.get(attempt_id)
        if previous is None:
            latest_by_attempt[attempt_id] = row
            continue
        prev_ts = parse_timestamp(previous.get("timestamp"))
        row_ts = parse_timestamp(row.get("timestamp"))
        if prev_ts is None or (row_ts is not None and row_ts >= prev_ts):
            latest_by_attempt[attempt_id] = row
    return sorted(
        latest_by_attempt.values(),
        key=lambda row: parse_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _risk_rank(value: str) -> int:
    if value == "HIGH":
        return 0
    if value == "MEDIUM":
        return 1
    return 2


def _case_status_rank(value: str) -> int:
    if value == "ESCALATED":
        return 0
    if value == "UNDER_INVESTIGATION":
        return 1
    if value == "TRIAGED":
        return 2
    if value == "NEW":
        return 3
    if value in COMPLETED_CASE_STATUSES:
        return 4
    return 7


def _queue_status(case_record: Optional[InvestigationCase]) -> str:
    status = getattr(case_record, "status", None) or "NEW"
    if status in {"CLEARED", "FALSE_POSITIVE"}:
        return "RESOLVED"
    return status


def _resolved_case_status(status: str) -> bool:
    return status in COMPLETED_CASE_STATUSES


def _actionable_case_status(status: str) -> bool:
    return status in ACTIONABLE_CASE_STATUSES


def _latest_event_metadata(db) -> Dict[str, RawExamEvent]:
    rows = (
        db.query(RawExamEvent)
        .order_by(RawExamEvent.received_at.desc(), RawExamEvent.id.desc())
        .all()
    )
    by_attempt: Dict[str, RawExamEvent] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in by_attempt:
            by_attempt[row.attempt_id] = row
    return by_attempt


def _latest_risk_history_by_attempt(db) -> Dict[str, RiskHistory]:
    rows = (
        db.query(RiskHistory)
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .all()
    )
    by_attempt: Dict[str, RiskHistory] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in by_attempt:
            by_attempt[row.attempt_id] = row
    return by_attempt


def _latest_attempt_log_by_attempt(db) -> Dict[str, AttemptLog]:
    rows = (
        db.query(AttemptLog)
        .order_by(AttemptLog.id.desc())
        .all()
    )
    by_attempt: Dict[str, AttemptLog] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in by_attempt:
            by_attempt[row.attempt_id] = row
    return by_attempt


def _build_dashboard_summary(db, *, recent_hours: Optional[int] = None) -> Dict[str, Any]:
    _sync_cases_from_attempts(db)
    latest_event_by_attempt = _latest_event_metadata(db)
    latest_history_by_attempt = _latest_risk_history_by_attempt(db)
    latest_logs_by_attempt = _latest_attempt_log_by_attempt(db)
    cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
    cutoff = _recent_cutoff(recent_hours)
    case_by_attempt = {case.attempt_id: case for case in cases if case.attempt_id}
    attempt_ids = sorted(
        {
            *(attempt_id for attempt_id in case_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_event_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_history_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_logs_by_attempt.keys() if attempt_id),
        }
    )

    attempt_rows: List[Dict[str, Any]] = []
    for attempt_id in attempt_ids:
        latest_event = latest_event_by_attempt.get(attempt_id)
        latest_history = latest_history_by_attempt.get(attempt_id)
        latest_log = latest_logs_by_attempt.get(attempt_id)
        case_record = case_by_attempt.get(attempt_id)
        queue_status = _queue_status(case_record)
        assessment = build_final_risk_assessment(
            attempt_id=attempt_id,
            latest_history=latest_history,
            latest_attempt_log=latest_log,
            latest_event=latest_event,
            current_risk=None,
            fallback_result={
                "candidate_id": getattr(case_record, "candidate_id", None),
                "candidate_name": getattr(case_record, "candidate_name", None),
                "candidate_email": getattr(case_record, "candidate_email", None),
                "assessment_id": getattr(case_record, "assessment_id", None),
                "assessment_name": getattr(case_record, "assessment_name", None),
                "risk": getattr(case_record, "current_risk", None),
                "confidence": getattr(case_record, "current_confidence", None),
                "combined_score": getattr(case_record, "current_combined_score", None),
                "generated_at": getattr(case_record, "latest_event_at", None),
            } if case_record is not None else None,
        )
        last_activity = (
            assessment.get("generated_at")
            or getattr(case_record, "latest_event_at", None)
            or getattr(latest_event, "received_at", None)
            or getattr(latest_event, "occurred_at", None)
            or getattr(latest_log, "timestamp", None)
        )
        attempt_rows.append(
            {
                "attempt_id": attempt_id,
                "candidate_name": assessment.get("candidate_name"),
                "candidate_email": assessment.get("candidate_email"),
                "assessment_name": assessment.get("assessment_name"),
                "combined_score": assessment.get("combined_score"),
                "confidence": assessment.get("confidence"),
                "risk_level": assessment.get("risk_level"),
                "strongest_reason": assessment.get("strongest_reason"),
                "status": queue_status,
                "timestamp": last_activity,
                "features": getattr(latest_log, "features", None) or {},
                "latest_event_type": getattr(latest_event, "event_type", None),
            }
        )

    if cutoff is not None:
        recent_rows = [
            row for row in attempt_rows
            if _is_recent_timestamp(row.get("timestamp"), cutoff)
        ]
        if recent_rows:
            attempt_rows = recent_rows
            recent_attempt_ids = {row.get("attempt_id") for row in attempt_rows if row.get("attempt_id")}
            cases = [
                case for case in cases
                if case.attempt_id in recent_attempt_ids
                or _is_recent_timestamp(getattr(case, "latest_event_at", None), cutoff)
                or _is_recent_timestamp(getattr(case, "updated_at", None), cutoff)
            ]

    attempt_rows.sort(
        key=lambda row: parse_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    total_attempts = len(attempt_rows)
    active_sessions = sum(
        1
        for row in attempt_rows
        if (
            not bool((row.get("features") or {}).get("has_submit_event"))
            and row.get("latest_event_type") != "exam_submitted"
            and _actionable_case_status(str(row.get("status") or "NEW"))
        )
    )
    high_risk_count = sum(
        1
        for row in attempt_rows
        if row.get("risk_level") == "HIGH" and _actionable_case_status(str(row.get("status") or "NEW"))
    )
    medium_risk_count = sum(1 for row in attempt_rows if row.get("risk_level") == "MEDIUM")
    low_risk_count = sum(1 for row in attempt_rows if row.get("risk_level") == "LOW")
    actionable_cases = [
        case for case in cases
        if _actionable_case_status(str(getattr(case, "status", None) or "NEW"))
    ]
    needs_review_count = len(actionable_cases)
    escalated_count = sum(1 for case in cases if getattr(case, "status", None) == "ESCALATED")
    avg_confidence = round(
        sum(safe_float(row.get("confidence"), 0.0) for row in attempt_rows) / max(1, total_attempts),
        4,
    )

    latest_log_time = max((parse_timestamp(row.get("timestamp")) for row in attempt_rows if row.get("timestamp")), default=None)
    if latest_log_time is not None:
        window_start = latest_log_time - timedelta(minutes=15)
        raw_event_times = [
            parse_timestamp(event.occurred_at)
            for event in db.query(RawExamEvent.occurred_at).all()
        ]
        recent_event_count = sum(
            1
            for event_time in raw_event_times
            if event_time is not None and event_time >= window_start and event_time <= latest_log_time
        )
        live_event_rate = max(0, round(recent_event_count / 15))
    else:
        live_event_rate = 0

    feed_candidates: List[Dict[str, Any]] = []
    for preferred_risk in ("HIGH", "MEDIUM", "LOW"):
        match = next((row for row in attempt_rows if row.get("risk_level") == preferred_risk), None)
        if match and match not in feed_candidates:
            feed_candidates.append(match)
    for row in attempt_rows:
        if row not in feed_candidates:
            feed_candidates.append(row)
        if len(feed_candidates) >= 8:
            break

    recent_risk_feed = []
    for row in feed_candidates[:8]:
        event_meta = latest_event_by_attempt.get(row.get("attempt_id"))
        recent_risk_feed.append({
            "attempt_id": row.get("attempt_id"),
            "candidate_name": row.get("candidate_name") or getattr(event_meta, "candidate_name", None),
            "candidate_email": row.get("candidate_email") or getattr(event_meta, "candidate_email", None),
            "timestamp": row.get("timestamp") or getattr(event_meta, "received_at", None) or getattr(event_meta, "occurred_at", None),
            "risk_level": row.get("risk_level") or "LOW",
            "risk": row.get("risk_level") or "LOW",
            "score": round(safe_float(row.get("combined_score"), 0.0), 4),
            "confidence": round(safe_float(row.get("confidence"), 0.0), 4),
            "attempt_summary": build_reason({
                "risk": row.get("risk_level") or "LOW",
                "explanation": row.get("strongest_reason"),
            }),
            "latest_event_type": getattr(event_meta, "event_type", None),
        })

    cases_needing_review: List[Dict[str, Any]] = []
    candidate_rows = []
    for row in attempt_rows:
        attempt_id = row.get("attempt_id")
        if not attempt_id:
            continue
        case_record = case_by_attempt.get(attempt_id)
        queue_status = _queue_status(case_record)
        if not _actionable_case_status(queue_status):
            continue
        event_meta = latest_event_by_attempt.get(attempt_id)
        candidate_rows.append({
            "attempt_id": attempt_id,
            "candidate_name": row.get("candidate_name") or getattr(event_meta, "candidate_name", None),
            "candidate_email": row.get("candidate_email") or getattr(event_meta, "candidate_email", None),
            "assessment_name": row.get("assessment_name") or getattr(event_meta, "assessment_name", None),
            "risk_level": row.get("risk_level") or "LOW",
            "risk": row.get("risk_level") or "LOW",
            "score": round(safe_float(row.get("combined_score"), 0.0), 4),
            "status": queue_status,
            "assigned_to": f"Reviewer #{case_record.assigned_reviewer_id}" if getattr(case_record, "assigned_reviewer_id", None) else "Unassigned",
            "last_activity": row.get("timestamp") or getattr(event_meta, "received_at", None) or getattr(case_record, "latest_event_at", None),
        })
    candidate_rows.sort(
        key=lambda item: (
            _case_status_rank(item["status"]),
            _risk_rank(item["risk_level"]),
            -item["score"],
            -(parse_timestamp(item["last_activity"]).timestamp() if parse_timestamp(item["last_activity"]) else 0),
        )
    )
    for index, item in enumerate(candidate_rows[:5], start=1):
        cases_needing_review.append({"priority": index, **item})

    risk_distribution = {
        "total_attempts": total_attempts,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "low_risk_count": low_risk_count,
    }

    recent_evidence_signals = {
        "clipboard_copy_paste": sum(int(safe_float((row.get("features") or {}).get("paste_count"), 0.0)) for row in attempt_rows),
        "tab_switch_events": sum(int(safe_float((row.get("features") or {}).get("tab_hidden_count"), 0.0)) for row in attempt_rows),
        "idle_time_spikes": sum(int(safe_float((row.get("features") or {}).get("idle_spike_count"), 0.0)) for row in attempt_rows),
        "rapid_answer_bursts": sum(1 for row in attempt_rows if safe_float((row.get("features") or {}).get("time_per_question_mean_s"), 0.0) > 0 and safe_float((row.get("features") or {}).get("time_per_question_mean_s"), 0.0) <= 15),
        "focus_blur_events": sum(int(safe_float((row.get("features") or {}).get("tab_hidden_count"), 0.0)) for row in attempt_rows),
    }

    system_health_basic = {
        "backend_api": "Healthy",
        "websocket_stream": "Connected" if latest_log_time is not None else "Disconnected",
        "event_stream": "Receiving" if live_event_rate > 0 else "Idle",
        "authentication": "Active",
        "database": "Healthy",
        "last_updated": latest_log_time.isoformat() if latest_log_time else None,
    }

    return {
        "active_sessions": active_sessions,
        "total_attempts": total_attempts,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "low_risk_count": low_risk_count,
        "needs_review_count": needs_review_count,
        "escalated_count": escalated_count,
        "avg_confidence": avg_confidence,
        "live_event_rate": live_event_rate,
        "recent_risk_feed": recent_risk_feed,
        "cases_needing_review": cases_needing_review,
        "risk_distribution": risk_distribution,
        "recent_evidence_signals": recent_evidence_signals,
        "system_health_basic": system_health_basic,
    }


@app.get("/v1/dashboard/summary")
def get_dashboard_summary(recent_hours: Optional[int] = 24, _user=Depends(require_reviewer)):
    db = SessionLocal()
    try:
        data = _build_dashboard_summary(db, recent_hours=recent_hours)
        return {
            "status": "success",
            "data": data,
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "status": "error",
            "message": str(e),
            "data": None,
        }
    finally:
        db.close()


@app.get("/v1/review-queue")
def get_review_queue(
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    assigned_to: Optional[str] = None,
    search: Optional[str] = None,
    recent_hours: Optional[int] = 24,
    _user=Depends(require_reviewer),
):
    db = SessionLocal()
    try:
        return _build_review_queue_payload(
            db,
            status_filter=status,
            risk_level_filter=risk_level,
            assigned_to_filter=assigned_to,
            search=search,
            recent_hours=recent_hours,
        )
    finally:
        db.close()


def _build_attempt_report(db, attempt_id: str) -> Dict[str, Any]:
    events = (
        db.query(RawExamEvent)
        .filter(RawExamEvent.attempt_id == attempt_id)
        .order_by(RawExamEvent.occurred_at.asc())
        .all()
    )
    history = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.asc())
        .all()
    )
    latest_attempt_log = (
        db.query(AttemptLog)
        .filter(AttemptLog.attempt_id == attempt_id)
        .order_by(AttemptLog.id.desc())
        .first()
    )
    current_risk = get_current_risk(attempt_id) or {}

    if not events and not history and not latest_attempt_log and not current_risk:
        return {}

    scoring_events = db_events_to_scoring_events(events)
    fallback_result = None
    if not current_risk and scoring_events:
        scoring_result = run_scoring(scoring_events, attempt_id)
        fallback_result = {
            "attempt_id": attempt_id,
            "risk": scoring_result.get("risk"),
            "confidence": scoring_result.get("confidence"),
            "combined_score": scoring_result.get("combined_score"),
            "explanation": scoring_result.get("explanation"),
            "strongest_reason": scoring_result.get("explanation"),
            "event_count": len(scoring_events),
            "session_intelligence": scoring_result.get("session_intelligence") or {},
        }

    latest_history = history[-1] if history else None
    features = getattr(latest_attempt_log, "features", None) or {}
    latest_event = events[-1] if events else None
    assessment = build_final_risk_assessment(
        attempt_id=attempt_id,
        latest_history=latest_history,
        latest_attempt_log=latest_attempt_log,
        latest_event=latest_event,
        current_risk=current_risk,
        fallback_result=fallback_result,
    )
    risk_score = safe_float(assessment.get("combined_score"), 0.0)
    confidence = safe_float(assessment.get("confidence"), 0.0)
    risk_level = str(assessment.get("risk_level") or normalize_risk_level(risk_score))

    event_payloads = [
        {
            "event_type": event.event_type,
            "payload": event.payload or {},
            "occurred_at": event.occurred_at,
        }
        for event in events
    ]
    evidence_items = normalize_evidence(
        events=event_payloads,
        features=features,
        risk_score=risk_score,
        risk_level=risk_level,
    )
    metadata_source = fallback_result or current_risk or {}
    session_narrative = (
        metadata_source.get("session_intelligence", {}) or {}
    ).get("session_narrative") or assessment.get("strongest_reason") or getattr(latest_history, "reason", None) or ""
    summary = build_report_summary(
        risk_score=risk_score,
        confidence=confidence,
        evidence_items=evidence_items,
        session_narrative=session_narrative,
    )
    overview_counts = build_violation_overview_counts(events=event_payloads, features=features)

    first_event = events[0] if events else None

    return {
        **summary,
        "attempt_id": attempt_id,
        "event_count": len(events),
        "evidence_items": evidence_items,
        "investigation_insights": summary.get("investigation_insights", []),
        "violation_overview_counts": overview_counts,
        "candidate_name": assessment.get("candidate_name") or "Unknown Candidate",
        "candidate_email": assessment.get("candidate_email") or "No email available",
        "assessment_name": assessment.get("assessment_name") or "Python Coding Assessment",
        "latest_event_at": assessment.get("generated_at") or getattr(latest_event, "occurred_at", None),
        "started_at": getattr(first_event, "occurred_at", None),
        "raw_explanation": assessment.get("strongest_reason") or getattr(latest_history, "reason", None),
        "strongest_reason": assessment.get("strongest_reason") or summary.get("strongest_reason"),
        "final_risk_assessment": {
            "combined_score": round(risk_score, 4),
            "risk_level": risk_level,
            "confidence": round(confidence, 4),
            "strongest_reason": assessment.get("strongest_reason") or summary.get("strongest_reason"),
            "generated_at": assessment.get("generated_at"),
        },
    }


@app.get("/")
def home():
    return {
        "message": "Server running",
        "version": APP_VERSION,
        "websocket": "/ws/risk",
        "progressive_ingestion": "/v1/events/ingest",
        "live_risk": "/v1/live-risk/{attempt_id}",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "real-time-risk-intelligence-platform",
        "version": APP_VERSION,
        "mode": APP_MODE,
        "timestamp": utc_now(),
    }


@app.get("/health/deep")
def health_deep():
    database = _database_health()
    redis = redis_health()
    overall = "ok" if database.get("reachable") and redis.get("reachable") else "degraded"

    return {
        "status": overall,
        "service": "real-time-risk-intelligence-platform",
        "version": APP_VERSION,
        "mode": APP_MODE,
        "config": {
            "database_mode": current_database_mode(),
            "redis_mode": redis.get("mode"),
        },
        "checks": {
            "database": database,
            "redis": redis,
        },
        "timestamp": utc_now(),
    }


@app.post("/v1/score")
async def score(batch: EventBatch):
    db = SessionLocal()
    try:
        result = run_scoring(batch.events, batch.attempt_id)
        score_value = safe_float(result.get("combined_score"), 0.0)
        confidence_value = safe_float(result.get("confidence"), 0.0)
        risk_level = normalize_risk_level(score_value)
        reason = build_reason(result)
        timeline_points = list(result.get("timeline_points") or [])
        latest_timeline_point = _last_timeline_point(result)
        snapshot_timestamp = latest_timeline_point.get("timestamp") or utc_now()

        _persist_timeline_snapshots(
            db,
            attempt_id=batch.attempt_id,
            candidate_id=None,
            candidate_name=None,
            candidate_email=None,
            assessment_id=None,
            assessment_name=None,
            timeline_points=timeline_points,
            final_confidence=confidence_value,
        )

        latest_history = _latest_risk_history_entry(db, batch.attempt_id)
        _record_risk_snapshot_if_needed(
            db,
            attempt_id=batch.attempt_id,
            candidate_id=None,
            candidate_name=None,
            candidate_email=None,
            assessment_id=None,
            assessment_name=None,
            risk_level=risk_level,
            confidence=confidence_value,
            score=score_value,
            reason=reason,
            timestamp=snapshot_timestamp,
            force=_risk_history_gap_elapsed(latest_history, timestamp=snapshot_timestamp),
        )
        db.commit()

        await manager.broadcast({
            "type": "risk_update",
            "mode": "batch_score",
            "attempt_id": result.get("attempt_id"),
            "risk": risk_level,
            "confidence": confidence_value,
            "combined_score": score_value,
            "explanation": result.get("explanation"),
        })

        return {
            **result,
            "risk": risk_level,
            "confidence": confidence_value,
            "combined_score": score_value,
        }

    except Exception as e:
        db.rollback()
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        db.close()


@app.post("/v1/events/ingest")
async def ingest_event(event: ExamEventIngest):
    db = SessionLocal()

    try:
        if not event.event_type:
            raise HTTPException(status_code=400, detail="event_type is required")

        received_at = utc_now()

        raw_event = RawExamEvent(
            attempt_id=event.attempt_id,
            candidate_id=event.candidate_id,
            candidate_name=event.candidate_name,
            candidate_email=event.candidate_email,
            assessment_id=event.assessment_id,
            assessment_name=event.assessment_name,
            event_type=event.event_type,
            payload=event.payload,
            occurred_at=event.occurred_at,
            received_at=received_at,
        )

        db.add(raw_event)
        db.flush()

        redis_event = {
            "event_type": event.event_type,
            "payload": event.payload,
            "occurred_at": event.occurred_at,
        }

        append_event(event.attempt_id, redis_event)
        scoring_events = get_events(event.attempt_id)

        if not scoring_events:
            scoring_events = [
                {
                    "event_type": item.event_type,
                    "payload": item.payload or {},
                    "occurred_at": item.occurred_at,
                }
                for item in (
                    db.query(RawExamEvent.event_type, RawExamEvent.payload, RawExamEvent.occurred_at)
                    .filter(RawExamEvent.attempt_id == event.attempt_id)
                    .order_by(RawExamEvent.occurred_at.asc())
                    .all()
                )
            ]

        result = run_scoring(scoring_events, event.attempt_id)
        score_value = safe_float(result.get("combined_score"), 0.0)
        confidence_value = safe_float(result.get("confidence"), 0.0)
        risk_level = normalize_risk_level(score_value)
        reason = build_reason(result)
        timeline_points = list(result.get("timeline_points") or [])
        latest_timeline_point = _last_timeline_point(result)
        snapshot_timestamp = latest_timeline_point.get("timestamp") or event.occurred_at or received_at

        current_risk_state = {
            "attempt_id": event.attempt_id,
            "candidate_id": event.candidate_id,
            "candidate_name": event.candidate_name,
            "candidate_email": event.candidate_email,
            "assessment_id": event.assessment_id,
            "assessment_name": event.assessment_name,
            "risk": risk_level,
            "confidence": confidence_value,
            "combined_score": score_value,
            "explanation": result.get("explanation"),
            "event_count": len(scoring_events),
            "updated_at": received_at,
            "session_intelligence": result.get("session_intelligence") or {},
        }

        set_current_risk(event.attempt_id, current_risk_state)

        _persist_timeline_snapshots(
            db,
            attempt_id=event.attempt_id,
            candidate_id=event.candidate_id,
            candidate_name=event.candidate_name,
            candidate_email=event.candidate_email,
            assessment_id=event.assessment_id,
            assessment_name=event.assessment_name,
            timeline_points=timeline_points,
            final_confidence=confidence_value,
        )

        latest_history = _latest_risk_history_entry(db, event.attempt_id)
        _record_risk_snapshot_if_needed(
            db,
            attempt_id=event.attempt_id,
            candidate_id=event.candidate_id,
            candidate_name=event.candidate_name,
            candidate_email=event.candidate_email,
            assessment_id=event.assessment_id,
            assessment_name=event.assessment_name,
            risk_level=risk_level,
            confidence=confidence_value,
            score=score_value,
            reason=reason,
            timestamp=snapshot_timestamp,
            force=_risk_history_gap_elapsed(latest_history, timestamp=snapshot_timestamp),
        )
        db.commit()

        broadcast_payload = {
            "type": "risk_update",
            "mode": "progressive_ingest",
            "attempt_id": event.attempt_id,
            "candidate_id": event.candidate_id,
            "candidate_name": event.candidate_name,
            "candidate_email": event.candidate_email,
            "assessment_id": event.assessment_id,
            "assessment_name": event.assessment_name,
            "latest_event": {
                "event_type": event.event_type,
                "payload": event.payload,
                "occurred_at": event.occurred_at,
            },
            "risk": risk_level,
            "confidence": confidence_value,
            "combined_score": score_value,
            "explanation": result.get("explanation"),
            "event_count": len(scoring_events),
            "timeline_point": {
                "risk": risk_level,
                "combined_score": score_value,
                "score": score_value,
                "risk_level": risk_level,
                "reason": reason,
                "summary": reason,
                "trigger": latest_timeline_point.get("trigger") or reason,
                "confidence": confidence_value,
                "timestamp": snapshot_timestamp,
            },
        }

        await manager.broadcast(broadcast_payload)

        return {
            "status": "success",
            "message": "Event ingested into Redis + PostgreSQL and attempt re-scored",
            "attempt_id": event.attempt_id,
            "event_count": len(scoring_events),
            "current_risk": risk_level,
            "current_confidence": confidence_value,
            "current_score": score_value,
            "result": {
                **result,
                "risk": risk_level,
                "confidence": confidence_value,
                "combined_score": score_value,
            },
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to ingest event: {e}") from e

    finally:
        db.close()


@app.get("/v1/live-risk/{attempt_id}")
def get_live_risk(attempt_id: str, _user=Depends(require_reviewer)):
    data = get_current_risk(attempt_id)

    if not data:
        return {
            "status": "not_found",
            "attempt_id": attempt_id,
            "data": None,
        }

    return {
        "status": "success",
        "attempt_id": attempt_id,
        "data": data,
    }


@app.get("/v1/events/{attempt_id}")
def get_events_for_attempt(attempt_id: str, _user=Depends(require_reviewer)):
    db = SessionLocal()

    try:
        events = (
            db.query(RawExamEvent)
            .filter(RawExamEvent.attempt_id == attempt_id)
            .order_by(RawExamEvent.occurred_at.asc())
            .all()
        )

        data = [
            {
                "id": e.id,
                "attempt_id": e.attempt_id,
                "candidate_id": e.candidate_id,
                "candidate_name": e.candidate_name,
                "candidate_email": e.candidate_email,
                "assessment_id": e.assessment_id,
                "assessment_name": e.assessment_name,
                "event_type": e.event_type,
                "payload": e.payload,
                "occurred_at": e.occurred_at,
                "received_at": e.received_at,
            }
            for e in events
        ]

        return {
            "status": "success",
            "attempt_id": attempt_id,
            "count": len(data),
            "data": data,
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "status": "error",
            "message": str(e),
            "data": [],
        }

    finally:
        db.close()


@app.get("/v1/risk-history/{attempt_id}")
def get_risk_history(attempt_id: str, _user=Depends(require_reviewer)):
    db = SessionLocal()

    try:
        history = (
            db.query(RiskHistory)
            .filter(RiskHistory.attempt_id == attempt_id)
            .order_by(RiskHistory.timestamp.asc())
            .all()
        )

        data = [
            {
                "id": h.id,
                "attempt_id": h.attempt_id,
                "candidate_id": h.candidate_id,
                "candidate_name": h.candidate_name,
                "candidate_email": h.candidate_email,
                "assessment_id": h.assessment_id,
                "assessment_name": h.assessment_name,
                "risk": h.risk,
                "risk_level": h.risk or normalize_risk_level(h.combined_score),
                "confidence": safe_float(h.confidence, 0.0),
                "combined_score": safe_float(h.combined_score, 0.0),
                "score": safe_float(h.combined_score, 0.0),
                "reason": h.reason,
                "summary": h.reason,
                "trigger": h.reason,
                "timestamp": h.timestamp,
            }
            for h in history
        ]

        return {
            "status": "success",
            "attempt_id": attempt_id,
            "count": len(data),
            "data": data,
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "status": "error",
            "message": str(e),
            "data": [],
        }

    finally:
        db.close()


@app.get("/v1/reports/{attempt_id}")
def get_attempt_report(attempt_id: str, _user=Depends(require_reviewer)):
    db = SessionLocal()
    try:
        report = _build_attempt_report(db, attempt_id)
        if not report:
            return {
                "status": "not_found",
                "attempt_id": attempt_id,
                "data": None,
            }
        return {
            "status": "success",
            "attempt_id": attempt_id,
            "data": report,
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "status": "error",
            "attempt_id": attempt_id,
            "message": str(e),
            "data": None,
        }
    finally:
        db.close()


@app.websocket("/ws/risk")
async def websocket_risk(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/v1/logs")
def get_logs(_user=Depends(require_reviewer)):
    LOG_FILE = "logs/attempt_logs.jsonl"

    if not os.path.exists(LOG_FILE):
        return {
            "status": "no_logs",
            "data": []
        }

    data = []

    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                try:
                    data.append(json.loads(line))
                except Exception:
                    continue

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "data": []
        }

    return {
        "status": "success",
        "count": len(data),
        "data": data
    }
