from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback

from app.runtime_env import load_local_env

load_local_env()

from engine.core.scorer import run_scoring
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from engine.db.database import Base, engine, SessionLocal, current_database_mode, ensure_demo_schema, is_sqlite_url
from engine.db.models import AttemptLog, AttemptState, InvestigationCase, RawExamEvent, RiskHistory

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
from app.cases.routes import _build_review_queue_payload, _ensure_case_for_attempt, _sync_cases_from_attempts
from app.models.user import User  # noqa: F401 (ensures users table is registered on startup)
from app.services.evidence_service import (
    build_violation_overview_counts,
    normalize_evidence,
    normalize_risk_level,
    safe_float,
)
from app.services.demo_attempt_cleanup import (
    DEMO_ABANDONED_STATUS,
    DEMO_EXPIRED_MESSAGE,
    DEMO_EXPIRED_STATUS,
    expire_stale_demo_attempts_if_due,
)
from app.services.final_assessment_service import build_final_risk_assessment
from app.services.provenance_analysis_service import (
    analyze_answer_provenance,
    build_provenance_evidence_item,
    get_provenance_retrieval_diagnostics,
)
from app.services.report_summary_service import build_report_summary

RISK_HISTORY_SCORE_EPSILON = 0.01
RISK_HISTORY_CONFIDENCE_EPSILON = 0.05
RISK_HISTORY_MIN_GAP_SECONDS = 45
ATTEMPT_LOG_FILE = Path("logs/attempt_logs.jsonl")
APP_VERSION = "2.2.0"
APP_MODE = os.getenv("APP_MODE", "local-demo").strip() or "local-demo"
ACTIONABLE_CASE_STATUSES = {"NEW", "TRIAGED", "UNDER_INVESTIGATION", "ESCALATED"}
COMPLETED_CASE_STATUSES = {"CONFIRMED_RISK", "FALSE_POSITIVE", "CLEARED", "RESOLVED", "CLOSED", "COMPLETED"}
PERSIST_DEMO_DATA = os.getenv("PERSIST_DEMO_DATA", "true").strip().lower() in {"1", "true", "yes", "on"}
SUBMISSION_EVENT_TYPES = {"exam_submitted", "assessment_submitted", "submit", "completed"}
ACTIVE_SESSION_WINDOW_MINUTES = int(os.getenv("ACTIVE_SESSION_WINDOW_MINUTES", "5"))
PROVENANCE_FINALIZATION_TIMEOUT_SECONDS = int(os.getenv("PROVENANCE_FINALIZATION_TIMEOUT_SECONDS", "45"))
SQLITE_LOCK_RETRY_ATTEMPTS = int(os.getenv("SQLITE_LOCK_RETRY_ATTEMPTS", "5"))
SQLITE_LOCK_RETRY_BASE_DELAY_MS = int(os.getenv("SQLITE_LOCK_RETRY_BASE_DELAY_MS", "180"))
FEED_SUSPICIOUS_EVENT_TYPES = {
    "clipboard",
    "visibility_change",
    "idle_state",
    "rapid_answer_burst",
    "typing_burst",
    "typing_pause",
    "backspace_activity",
}
FEED_GENERIC_REASON_PHRASES = (
    "stable engagement observed",
    "normal behavior pattern",
    "review signals detected",
    "no major violation detected",
    "minor focus interruptions observed",
)
FEED_REASON_KEYWORDS = (
    "clipboard",
    "focus loss",
    "focus recovery",
    "tab switch",
    "visibility",
    "paste",
    "rapid answer",
    "typing",
    "idle",
    "correlated",
    "suspicious sequence",
    "review recommended",
    "escalat",
    "confidence spike",
    "overwrite",
)
logger = logging.getLogger("proctoriq.app")


def _is_local_mode() -> bool:
    mode = APP_MODE.strip().lower()
    return mode in {"local", "local-demo", "local-postgres-redis", "dev", "development", "docker-compose"}


def _is_controlled_demo_mode() -> bool:
    return APP_MODE.strip().lower() == "controlled-demo"


def _is_sqlite_lock_error(exc: Exception) -> bool:
    return is_sqlite_url() and "database is locked" in str(exc or "").lower()


def _run_with_sqlite_lock_retry(operation_name: str, func, *, attempts: int | None = None):
    max_attempts = max(1, attempts or SQLITE_LOCK_RETRY_ATTEMPTS)
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = func()
            if attempt > 1:
                logger.info("sqlite_lock_retry_succeeded operation=%s attempt=%s", operation_name, attempt)
            return result
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                logger.error("sqlite_lock_retry_exhausted operation=%s attempts=%s", operation_name, max_attempts)
                raise
            delay_seconds = (SQLITE_LOCK_RETRY_BASE_DELAY_MS * attempt) / 1000.0
            logger.warning("sqlite_lock_retry operation=%s attempt=%s/%s delay_ms=%s", operation_name, attempt, max_attempts, int(delay_seconds * 1000))
            time.sleep(delay_seconds)
    if last_exc is not None:
        raise last_exc


def _with_retry_session(operation_name: str, func, *, attempts: int | None = None):
    def _attempt():
        db = SessionLocal()
        try:
            return func(db)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return _run_with_sqlite_lock_retry(operation_name, _attempt, attempts=attempts)


def _attempt_data_exists(db) -> bool:
    return bool(db.query(AttemptLog.id).limit(1).first() or db.query(RawExamEvent.id).limit(1).first())


def _startup_counts(db) -> Dict[str, int]:
    return {
        "attempts": int(db.query(AttemptLog.attempt_id).distinct().count()),
        "events": int(db.query(RawExamEvent.id).count()),
        "cases": int(db.query(InvestigationCase.id).count()),
    }


def _maybe_seed_hosted_demo_dataset() -> None:
    if not _is_controlled_demo_mode() or not PERSIST_DEMO_DATA:
        return

    db = SessionLocal()
    try:
        if _attempt_data_exists(db):
            print("[SKIP] Hosted demo dataset already exists")
            print("[OK] Hosted demo dataset verified")
            return
    finally:
        db.close()

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_dataset.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--count", "100", "--seed", "42"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        print(f"[WARN] Hosted demo dataset seeding failed: {exc}")
        return

    if result.returncode == 0:
        print("[OK] Hosted demo dataset seeded")
        print("[OK] Hosted demo dataset verified")
        return

    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    details = stderr or stdout or f"exit code {result.returncode}"
    print(f"[WARN] Hosted demo dataset seeding failed: {details}")


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
        print(f"[OK] Database backend: {current_database_mode()}")
        print("[OK] Connection pool initialized")
        _log_provenance_retrieval_diagnostics()
        if should_verify_demo_admin_on_startup(APP_MODE):
            db = SessionLocal()
            try:
                ensure_demo_admin(db)
                print("[OK] Demo admin verified")
            finally:
                db.close()
        _maybe_seed_hosted_demo_dataset()
        db = SessionLocal()
        try:
            cleanup_result = expire_stale_demo_attempts_if_due(db, force=True)
            if cleanup_result["updated_count"]:
                db.commit()
            print(f"[OK] Stale demo attempts expired: {cleanup_result['updated_count']}")
            if _attempt_data_exists(db) and not db.query(InvestigationCase.id).limit(1).first():
                _sync_cases_from_attempts(db)
            counts = _startup_counts(db)
            print(f"[OK] Startup records: attempts={counts['attempts']} cases={counts['cases']} events={counts['events']}")
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


class SubmittedAnswerIn(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=255)
    question_title: Optional[str] = Field(default=None, max_length=255)
    section_id: Optional[str] = Field(default=None, max_length=255)
    section_title: Optional[str] = Field(default=None, max_length=255)
    assessment_type: Optional[str] = Field(default=None, max_length=255)
    input_type: Optional[str] = Field(default=None, max_length=64)
    answer_text: str = Field(..., min_length=1, max_length=12000)
    marked_for_review: bool = False


class SubmittedAnswersPayload(BaseModel):
    attempt_id: str = Field(..., min_length=1, max_length=255)
    candidate_id: Optional[str] = Field(default=None, max_length=255)
    candidate_name: Optional[str] = Field(default=None, max_length=255)
    candidate_email: Optional[str] = Field(default=None, max_length=320)
    assessment_id: Optional[str] = Field(default=None, max_length=255)
    assessment_name: Optional[str] = Field(default=None, max_length=255)
    answers: List[SubmittedAnswerIn] = Field(default_factory=list)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


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


def normalize_datetime_value(value: Any, *, field_name: str = "timestamp") -> Optional[datetime]:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    if value not in (None, "", {}):
        logger.warning(
            "datetime_parse_failed field_name=%s value_type=%s",
            field_name,
            type(value).__name__,
        )
    return None


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


def _behavioral_feed_timestamp(row: Dict[str, Any], latest_event: Optional[RawExamEvent]) -> Optional[datetime]:
    candidates = [
        getattr(latest_event, "occurred_at", None),
        getattr(latest_event, "received_at", None),
        ((row.get("latest_event") or {}).get("occurred_at") if isinstance(row.get("latest_event"), dict) else None),
        ((row.get("latest_event") or {}).get("timestamp") if isinstance(row.get("latest_event"), dict) else None),
        row.get("submitted_at"),
    ]
    for candidate in candidates:
        parsed = parse_timestamp(candidate)
        if parsed is not None:
            return parsed
    return None


def _meaningful_history_timestamp(row: Dict[str, Any], latest_history: Optional[RiskHistory]) -> Optional[datetime]:
    if latest_history is None:
        return None
    risk_level = str(row.get("risk_level") or row.get("risk") or getattr(latest_history, "risk", None) or "LOW").upper()
    reason = str(getattr(latest_history, "reason", None) or row.get("strongest_reason") or "").strip().lower()
    has_reason_keyword = any(keyword in reason for keyword in FEED_REASON_KEYWORDS)
    if risk_level in {"HIGH", "MEDIUM"} or has_reason_keyword:
        return parse_timestamp(getattr(latest_history, "timestamp", None))
    return None


def _row_has_meaningful_behavioral_signal(row: Dict[str, Any], latest_event: Optional[RawExamEvent]) -> bool:
    risk_level = str(row.get("risk_level") or row.get("risk") or "LOW").upper()
    latest_event_type = str(
        row.get("latest_event_type")
        or getattr(latest_event, "event_type", None)
        or ""
    ).strip().lower()
    reason = str(row.get("strongest_reason") or row.get("explanation") or "").strip().lower()
    features = row.get("features") or {}

    suspicious_sequences = int(features.get("suspicious_sequence_count") or features.get("correlated_pattern_count") or 0)
    clipboard_events = int(features.get("clipboard_count") or 0)
    focus_interruptions = int(features.get("focus_blur_count") or features.get("tab_hidden_count") or 0)
    typing_anomalies = int(features.get("typing_behavior_anomaly_count") or 0)
    rapid_answers = int(features.get("rapid_answer_count") or features.get("rapid_answer_burst_count") or 0)
    confidence = safe_float(row.get("confidence"), 0.0)

    has_reason_keyword = any(keyword in reason for keyword in FEED_REASON_KEYWORDS)
    is_generic_reason = reason and any(phrase in reason for phrase in FEED_GENERIC_REASON_PHRASES)
    has_suspicious_event = latest_event_type in FEED_SUSPICIOUS_EVENT_TYPES
    has_behavioral_counts = any(
        (
            suspicious_sequences > 0,
            clipboard_events > 0,
            focus_interruptions >= 3,
            typing_anomalies > 0,
            rapid_answers > 0,
        )
    )

    if risk_level == "HIGH" and (has_reason_keyword or has_suspicious_event or has_behavioral_counts):
        return True
    if risk_level == "MEDIUM" and (has_reason_keyword or has_suspicious_event or has_behavioral_counts or confidence >= 0.55):
        return True
    if suspicious_sequences > 0 or clipboard_events > 0 or (has_reason_keyword and not is_generic_reason):
        return True
    return False


def _feed_priority_score(row: Dict[str, Any], latest_event: Optional[RawExamEvent]) -> tuple[int, float]:
    risk_level = str(row.get("risk_level") or row.get("risk") or "LOW").upper()
    features = row.get("features") or {}
    latest_event_type = str(
        row.get("latest_event_type")
        or getattr(latest_event, "event_type", None)
        or ""
    ).strip().lower()

    risk_weight = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(risk_level, 0)
    suspicious_sequences = int(features.get("suspicious_sequence_count") or features.get("correlated_pattern_count") or 0)
    clipboard_events = int(features.get("clipboard_count") or 0)
    typing_anomalies = int(features.get("typing_behavior_anomaly_count") or 0)
    rapid_answers = int(features.get("rapid_answer_count") or features.get("rapid_answer_burst_count") or 0)
    event_bonus = 1 if latest_event_type in FEED_SUSPICIOUS_EVENT_TYPES else 0
    confidence = safe_float(row.get("confidence"), 0.0)
    return (
        risk_weight * 100
        + suspicious_sequences * 8
        + clipboard_events * 4
        + typing_anomalies * 3
        + rapid_answers * 3
        + event_bonus,
        confidence,
    )


def _feed_queue_priority(status: str) -> int:
    normalized = str(status or "").upper()
    if normalized == "ESCALATED":
        return 3
    if normalized == "UNDER_INVESTIGATION":
        return 2
    if normalized in {"TRIAGED", "NEW"}:
        return 1
    return 0


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


def _is_submission_event_type(event_type: Any) -> bool:
    return str(event_type or "").strip().lower() in SUBMISSION_EVENT_TYPES


def _active_session_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=max(1, ACTIVE_SESSION_WINDOW_MINUTES))


def _is_live_attempt_row(row: Dict[str, Any]) -> bool:
    attempt_status = str(row.get("attempt_status") or "").strip().upper()
    if attempt_status not in {"ONGOING", "IN_PROGRESS"}:
        return False
    if _is_submission_event_type(row.get("latest_event_type")):
        return False

    last_activity = parse_timestamp(row.get("timestamp"))
    if last_activity is None or last_activity < _active_session_cutoff():
        return False

    review_status = str(row.get("status") or "").strip().upper()
    if review_status in COMPLETED_CASE_STATUSES:
        return False
    return True


def _derive_attempt_status(
    *,
    existing_status: Optional[str] = None,
    review_status: Optional[str] = None,
    latest_event_type: Optional[str] = None,
    features: Optional[Dict[str, Any]] = None,
) -> str:
    normalized_existing = str(existing_status or "").strip().upper()
    if normalized_existing in {"RESOLVED", "COMPLETED"}:
        return "RESOLVED"
    if normalized_existing in {DEMO_EXPIRED_STATUS, DEMO_ABANDONED_STATUS}:
        return normalized_existing

    normalized_review = str(review_status or "").strip().upper()
    if normalized_review in COMPLETED_CASE_STATUSES:
        return "RESOLVED"
    if normalized_review in {"TRIAGED", "UNDER_INVESTIGATION", "ESCALATED"}:
        return "UNDER_REVIEW"

    if normalized_existing == "UNDER_REVIEW":
        return "UNDER_REVIEW"
    if normalized_existing == "SUBMITTED":
        return "SUBMITTED"

    if _is_submission_event_type(latest_event_type) or bool((features or {}).get("has_submit_event")):
        return "SUBMITTED"
    return "ONGOING"


def _serialize_risk_history_points(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for point in points:
        timestamp = point.get("timestamp")
        if not timestamp:
            continue
        serialized.append(
            {
                "timestamp": str(timestamp),
                "score": round(safe_float(point.get("score"), 0.0), 4),
                "combined_score": round(safe_float(point.get("score"), 0.0), 4),
                "risk_level": str(point.get("risk_level") or normalize_risk_level(point.get("score"))),
                "risk": str(point.get("risk_level") or normalize_risk_level(point.get("score"))),
                "confidence": round(safe_float(point.get("confidence"), 0.0), 4),
                "trigger": str(point.get("trigger") or point.get("reason") or "Behavioral milestone detected"),
                "reason": str(point.get("reason") or point.get("trigger") or "Behavioral milestone detected"),
                "summary": str(point.get("summary") or point.get("trigger") or point.get("reason") or "Behavioral milestone detected"),
            }
        )
    serialized.sort(
        key=lambda item: parse_timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
    )
    return serialized


def _latest_attempt_state_by_attempt(db) -> Dict[str, AttemptState]:
    rows = (
        db.query(AttemptState)
        .order_by(AttemptState.updated_at.desc(), AttemptState.id.desc())
        .all()
    )
    by_attempt: Dict[str, AttemptState] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in by_attempt:
            by_attempt[row.attempt_id] = row
    return by_attempt


def _upsert_attempt_log(
    db,
    *,
    attempt_id: str,
    risk_level: str,
    confidence: float,
    score: float,
    features: Optional[Dict[str, Any]],
    signals: Optional[Dict[str, Any]],
    timestamp: str,
) -> AttemptLog:
    entry = AttemptLog(
        attempt_id=attempt_id,
        risk=risk_level,
        confidence=confidence,
        confidence_score=confidence,
        combined_score=score,
        features=features or {},
        signals=signals or {},
        timestamp=timestamp,
    )
    db.add(entry)
    return entry


def _upsert_attempt_state(
    db,
    *,
    attempt_id: str,
    candidate_id: Optional[str],
    candidate_name: Optional[str],
    candidate_email: Optional[str],
    assessment_id: Optional[str],
    assessment_name: Optional[str],
    review_status: Optional[str],
    latest_event_type: Optional[str],
    latest_event_at: Optional[str],
    event_count: int,
    risk_level: str,
    score: float,
    confidence: float,
    strongest_reason: str,
    violation_overview: Dict[str, Any],
    evidence_summary: List[Dict[str, Any]],
    risk_history: List[Dict[str, Any]],
    features: Optional[Dict[str, Any]],
    signals: Optional[Dict[str, Any]],
) -> AttemptState:
    state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
    if state is None:
        state = AttemptState(attempt_id=attempt_id, updated_at=utc_now())
        db.add(state)

    prior_submitted_at = getattr(state, "submitted_at", None)
    next_submitted_at = prior_submitted_at
    if _is_submission_event_type(latest_event_type):
        next_submitted_at = latest_event_at or utc_now()

    final_status = _derive_attempt_status(
        existing_status=getattr(state, "status", None),
        review_status=review_status,
        latest_event_type=latest_event_type,
        features=features,
    )

    state.candidate_id = candidate_id or state.candidate_id
    state.candidate_name = candidate_name or state.candidate_name
    state.candidate_email = candidate_email or state.candidate_email
    state.assessment_id = assessment_id or state.assessment_id
    state.assessment_name = assessment_name or state.assessment_name
    state.status = final_status
    state.review_status = review_status or state.review_status
    state.final_risk_level = risk_level
    state.final_risk_score = score
    state.confidence = confidence
    if strongest_reason or not state.strongest_reason:
        state.strongest_reason = strongest_reason
    if violation_overview or not state.violation_overview:
        state.violation_overview = violation_overview or {}
    if evidence_summary or not state.evidence_summary:
        state.evidence_summary = evidence_summary or []
    if risk_history or not state.risk_history:
        state.risk_history = risk_history or []
    if features or not state.features:
        state.features = features or {}
    merged_signals = dict(getattr(state, "signals", None) or {})
    if signals:
        merged_signals.update(signals)
    if merged_signals or not state.signals:
        state.signals = merged_signals
    state.latest_event_type = latest_event_type or state.latest_event_type
    state.latest_event_at = latest_event_at or state.latest_event_at
    state.event_count = max(int(event_count or 0), int(state.event_count or 0))
    state.submitted_at = next_submitted_at
    state.updated_at = utc_now()
    return state


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


def _latest_meaningful_event_metadata(db) -> Dict[str, RawExamEvent]:
    rows = (
        db.query(RawExamEvent)
        .filter(RawExamEvent.event_type.in_(sorted(FEED_SUSPICIOUS_EVENT_TYPES)))
        .order_by(RawExamEvent.occurred_at.desc(), RawExamEvent.received_at.desc(), RawExamEvent.id.desc())
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


def _event_counts_by_attempt(db) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for (attempt_id,) in db.query(RawExamEvent.attempt_id).all():
        if not attempt_id:
            continue
        counts[attempt_id] = counts.get(attempt_id, 0) + 1
    return counts


def _build_persisted_attempt_rows(db, *, recent_hours: Optional[int] = None, sync_cases: bool = False) -> List[Dict[str, Any]]:
    if sync_cases:
        _sync_cases_from_attempts(db)
    latest_event_by_attempt = _latest_event_metadata(db)
    latest_history_by_attempt = _latest_risk_history_by_attempt(db)
    latest_logs_by_attempt = _latest_attempt_log_by_attempt(db)
    latest_states_by_attempt = _latest_attempt_state_by_attempt(db)
    event_counts = _event_counts_by_attempt(db)
    cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
    cutoff = _recent_cutoff(recent_hours)
    case_by_attempt = {case.attempt_id: case for case in cases if case.attempt_id}
    attempt_ids = sorted(
        {
            *(attempt_id for attempt_id in latest_states_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in case_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_event_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_history_by_attempt.keys() if attempt_id),
            *(attempt_id for attempt_id in latest_logs_by_attempt.keys() if attempt_id),
        }
    )

    attempt_rows: List[Dict[str, Any]] = []
    for attempt_id in attempt_ids:
        attempt_state = latest_states_by_attempt.get(attempt_id)
        latest_event = latest_event_by_attempt.get(attempt_id)
        latest_history = latest_history_by_attempt.get(attempt_id)
        latest_log = latest_logs_by_attempt.get(attempt_id)
        case_record = case_by_attempt.get(attempt_id)
        queue_status = _queue_status(case_record)
        assessment = build_final_risk_assessment(
            attempt_id=attempt_id,
            persisted_state=attempt_state,
            latest_history=latest_history,
            latest_attempt_log=latest_log,
            latest_event=latest_event,
            current_risk=None,
            fallback_result={
                "candidate_id": getattr(attempt_state, "candidate_id", None) or getattr(case_record, "candidate_id", None),
                "candidate_name": getattr(attempt_state, "candidate_name", None) or getattr(case_record, "candidate_name", None),
                "candidate_email": getattr(attempt_state, "candidate_email", None) or getattr(case_record, "candidate_email", None),
                "assessment_id": getattr(attempt_state, "assessment_id", None) or getattr(case_record, "assessment_id", None),
                "assessment_name": getattr(attempt_state, "assessment_name", None) or getattr(case_record, "assessment_name", None),
                "risk": getattr(attempt_state, "final_risk_level", None) or getattr(case_record, "current_risk", None),
                "confidence": getattr(attempt_state, "confidence", None) or getattr(case_record, "current_confidence", None),
                "combined_score": getattr(attempt_state, "final_risk_score", None) or getattr(case_record, "current_combined_score", None),
                "generated_at": getattr(attempt_state, "updated_at", None) or getattr(case_record, "latest_event_at", None),
                "strongest_reason": getattr(attempt_state, "strongest_reason", None),
            } if attempt_state is not None or case_record is not None else None,
        )
        last_activity = (
            getattr(attempt_state, "updated_at", None)
            or getattr(attempt_state, "latest_event_at", None)
            or
            assessment.get("generated_at")
            or getattr(case_record, "latest_event_at", None)
            or getattr(latest_event, "received_at", None)
            or getattr(latest_event, "occurred_at", None)
            or getattr(latest_log, "timestamp", None)
        )
        attempt_status = _derive_attempt_status(
            existing_status=getattr(attempt_state, "status", None),
            review_status=queue_status,
            latest_event_type=getattr(attempt_state, "latest_event_type", None) or getattr(latest_event, "event_type", None),
            features=(getattr(attempt_state, "features", None) or getattr(latest_log, "features", None) or {}),
        )
        attempt_rows.append(
            {
                "attempt_id": attempt_id,
                "candidate_id": assessment.get("candidate_id"),
                "candidate_name": assessment.get("candidate_name") or getattr(latest_event, "candidate_name", None),
                "candidate_email": assessment.get("candidate_email") or getattr(latest_event, "candidate_email", None),
                "assessment_id": assessment.get("assessment_id"),
                "assessment_name": assessment.get("assessment_name") or getattr(latest_event, "assessment_name", None),
                "combined_score": round(safe_float(assessment.get("combined_score"), 0.0), 4),
                "confidence": round(safe_float(assessment.get("confidence"), 0.0), 4),
                "risk": assessment.get("risk_level") or "LOW",
                "risk_level": assessment.get("risk_level") or "LOW",
                "strongest_reason": assessment.get("strongest_reason") or "",
                "explanation": assessment.get("strongest_reason") or "",
                "explanation_text": assessment.get("strongest_reason") or "",
                "status": queue_status,
                "attempt_status": attempt_status,
                "timestamp": last_activity,
                "features": getattr(attempt_state, "features", None) or getattr(latest_log, "features", None) or {},
                "signals": getattr(attempt_state, "signals", None) or getattr(latest_log, "signals", None) or {},
                "event_count": max(event_counts.get(attempt_id, 0), int(getattr(attempt_state, "event_count", 0) or 0)),
                "latest_event_type": getattr(attempt_state, "latest_event_type", None) or getattr(latest_event, "event_type", None),
                "submitted_at": getattr(attempt_state, "submitted_at", None),
                "latest_event": (
                    {
                        "event_type": getattr(attempt_state, "latest_event_type", None) or latest_event.event_type,
                        "occurred_at": getattr(attempt_state, "latest_event_at", None) or latest_event.occurred_at,
                        "timestamp": getattr(attempt_state, "updated_at", None) or latest_event.received_at,
                        "payload": latest_event.payload or {},
                    }
                    if latest_event is not None
                    else (
                        {
                            "event_type": getattr(attempt_state, "latest_event_type", None),
                            "occurred_at": getattr(attempt_state, "latest_event_at", None),
                            "timestamp": getattr(attempt_state, "updated_at", None),
                            "payload": {},
                        }
                        if attempt_state is not None
                        else None
                    )
                ),
            }
        )

    if cutoff is not None:
        recent_rows = [
            row for row in attempt_rows
            if _is_recent_timestamp(row.get("timestamp"), cutoff)
        ]
        if recent_rows:
            attempt_rows = recent_rows

    attempt_rows.sort(
        key=lambda row: parse_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return attempt_rows


def _build_dashboard_summary(db, *, recent_hours: Optional[int] = None) -> Dict[str, Any]:
    cleanup_result = expire_stale_demo_attempts_if_due(db)
    if cleanup_result["updated_count"]:
        db.commit()
    latest_event_by_attempt = _latest_event_metadata(db)
    latest_meaningful_event_by_attempt = _latest_meaningful_event_metadata(db)
    latest_history_by_attempt = _latest_risk_history_by_attempt(db)
    cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
    cutoff = _recent_cutoff(recent_hours)
    attempt_rows = _build_persisted_attempt_rows(db, recent_hours=recent_hours, sync_cases=False)
    if cutoff is not None and attempt_rows:
        recent_attempt_ids = {row.get("attempt_id") for row in attempt_rows if row.get("attempt_id")}
        cases = [
            case for case in cases
            if case.attempt_id in recent_attempt_ids
            or _is_recent_timestamp(getattr(case, "latest_event_at", None), cutoff)
            or _is_recent_timestamp(getattr(case, "updated_at", None), cutoff)
        ]
    case_by_attempt = {case.attempt_id: case for case in cases if case.attempt_id}

    attempt_rows.sort(
        key=lambda row: parse_timestamp(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    total_attempts = len(attempt_rows)
    active_sessions = sum(
        1
        for row in attempt_rows
        if _is_live_attempt_row(row)
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

    meaningful_feed_candidates: List[Dict[str, Any]] = []
    for row in attempt_rows:
        attempt_id = row.get("attempt_id")
        event_meta = latest_event_by_attempt.get(attempt_id)
        meaningful_event_meta = latest_meaningful_event_by_attempt.get(attempt_id)
        latest_history = latest_history_by_attempt.get(attempt_id)
        if not _row_has_meaningful_behavioral_signal(row, meaningful_event_meta or event_meta):
            continue
        feed_event_time = max(
            (
                ts for ts in [
                    _behavioral_feed_timestamp(row, meaningful_event_meta or event_meta),
                    _meaningful_history_timestamp(row, latest_history),
                ]
                if ts is not None
            ),
            default=None,
        )
        meaningful_feed_candidates.append(
            {
                **row,
                "_feed_event_time": feed_event_time,
                "_feed_priority": _feed_priority_score(row, meaningful_event_meta or event_meta),
                "_feed_queue_priority": _feed_queue_priority(str(row.get("status") or "NEW")),
                "_event_meta": meaningful_event_meta or event_meta,
            }
        )

    meaningful_feed_candidates.sort(
        key=lambda row: (
            row.get("_feed_event_time") or datetime.min.replace(tzinfo=timezone.utc),
            row.get("_feed_queue_priority", 0),
            row.get("_feed_priority", (0, 0.0))[0],
            row.get("_feed_priority", (0, 0.0))[1],
        ),
        reverse=True,
    )

    recent_risk_feed = []
    for row in meaningful_feed_candidates[:8]:
        event_meta = row.get("_event_meta")
        feed_timestamp = row.get("_feed_event_time")
        recent_risk_feed.append({
            "attempt_id": row.get("attempt_id"),
            "candidate_name": row.get("candidate_name") or getattr(event_meta, "candidate_name", None),
            "candidate_email": row.get("candidate_email") or getattr(event_meta, "candidate_email", None),
            "timestamp": feed_timestamp.isoformat() if isinstance(feed_timestamp, datetime) else getattr(event_meta, "occurred_at", None) or getattr(event_meta, "received_at", None),
            "assessment_name": row.get("assessment_name") or getattr(event_meta, "assessment_name", None),
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
    cleanup_result = expire_stale_demo_attempts_if_due(db)
    if cleanup_result["updated_count"]:
        db.commit()
    persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
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

    if not events and not history and not latest_attempt_log and not current_risk and not persisted_state:
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
        persisted_state=persisted_state,
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
    computed_evidence_items = normalize_evidence(
        events=event_payloads,
        features=features,
        risk_score=risk_score,
        risk_level=risk_level,
    )
    provenance_result = dict(((getattr(persisted_state, "signals", None) or {}).get("answer_provenance") or {}))
    persisted_evidence_items = list(getattr(persisted_state, "evidence_summary", None) or [])
    evidence_items = persisted_evidence_items if persisted_evidence_items else computed_evidence_items
    provenance_evidence = build_provenance_evidence_item(provenance_result)
    if provenance_evidence is not None:
        evidence_items = [provenance_evidence, *evidence_items]
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
    hybrid_reasoning = _build_hybrid_reasoning_context(
        final_risk_level=risk_level,
        session_intelligence=dict(metadata_source.get("session_intelligence", {}) or {}),
        recommendation=str(summary.get("recommendation") or ""),
        why_this_score=str(summary.get("why_this_score") or ""),
    )
    computed_overview_counts = build_violation_overview_counts(events=event_payloads, features=features)
    persisted_overview_counts = dict(getattr(persisted_state, "violation_overview", None) or {})
    overview_counts = persisted_overview_counts if persisted_overview_counts else computed_overview_counts

    first_event = events[0] if events else None
    attempt_status = _derive_attempt_status(
        existing_status=getattr(persisted_state, "status", None),
        review_status=getattr(persisted_state, "review_status", None),
        latest_event_type=getattr(persisted_state, "latest_event_type", None) or getattr(latest_event, "event_type", None),
        features=(getattr(persisted_state, "features", None) or features),
    )

    return {
        **summary,
        "summary_text": hybrid_reasoning.get("summary_text") or summary.get("recommendation"),
        "why_score_text": hybrid_reasoning.get("why_score_text") or summary.get("why_this_score"),
        "hybrid_reasoning": {
            "ml_advisory_level": hybrid_reasoning.get("ml_advisory_level"),
            "deterministic_risk_level": hybrid_reasoning.get("deterministic_risk_level"),
            "behavioral_correlation_strength": hybrid_reasoning.get("behavioral_correlation_strength"),
            "escalation_reason": hybrid_reasoning.get("escalation_reason"),
        },
        "attempt_id": attempt_id,
        "event_count": max(len(events), int(getattr(persisted_state, "event_count", 0) or 0)),
        "evidence_items": evidence_items,
        "investigation_insights": summary.get("investigation_insights", []),
        "violation_overview_counts": overview_counts,
        "candidate_name": assessment.get("candidate_name") or "Unknown Candidate",
        "candidate_email": assessment.get("candidate_email") or "No email available",
        "assessment_name": assessment.get("assessment_name") or "Python Coding Assessment",
        "latest_event_at": getattr(persisted_state, "latest_event_at", None) or assessment.get("generated_at") or getattr(latest_event, "occurred_at", None),
        "started_at": getattr(first_event, "occurred_at", None),
        "raw_explanation": assessment.get("strongest_reason") or getattr(latest_history, "reason", None),
        "strongest_reason": assessment.get("strongest_reason") or summary.get("strongest_reason"),
        "attempt_status": attempt_status,
        "attempt_status_note": DEMO_EXPIRED_MESSAGE if attempt_status in {DEMO_EXPIRED_STATUS, DEMO_ABANDONED_STATUS} else None,
        "submitted_at": getattr(persisted_state, "submitted_at", None),
        "updated_at": getattr(persisted_state, "updated_at", None) or assessment.get("generated_at"),
        "provenance_analysis": provenance_result or None,
        "signals": dict(getattr(persisted_state, "signals", None) or {}),
        "final_risk_assessment": {
            "combined_score": round(risk_score, 4),
            "risk_level": risk_level,
            "confidence": round(confidence, 4),
            "strongest_reason": assessment.get("strongest_reason") or summary.get("strongest_reason"),
            "generated_at": getattr(persisted_state, "updated_at", None) or assessment.get("generated_at"),
        },
    }


def _log_provenance_retrieval_diagnostics() -> None:
    diagnostics = get_provenance_retrieval_diagnostics()
    logger.info(
        "provenance_retrieval_config experimental_web_retrieval_enabled=%s tavily_key_present=%s serper_key_present=%s brave_key_present=%s selected_retrieval_provider=%s max_results=%s timeout_seconds=%s",
        diagnostics["experimental_web_retrieval_enabled"],
        diagnostics["tavily_key_present"],
        diagnostics["serper_key_present"],
        diagnostics["brave_key_present"],
        diagnostics["selected_retrieval_provider"],
        diagnostics["max_results"],
        diagnostics["timeout_seconds"],
    )


def _is_public_demo_attempt_id(attempt_id: str) -> bool:
    normalized = str(attempt_id or "").strip().lower()
    return normalized.startswith("demo_public_")


def _finalize_stale_provenance_state(db, persisted_state: AttemptState) -> dict[str, Any]:
    signals = dict(getattr(persisted_state, "signals", None) or {})
    provenance_status = str(signals.get("provenance_status") or "").strip().lower()
    submitted_answers = list(signals.get("submitted_answers") or [])
    if provenance_status != "processing" or submitted_answers == []:
        return signals

    started_at = (
        normalize_datetime_value(signals.get("provenance_started_at"), field_name="provenance_started_at")
        or normalize_datetime_value(getattr(persisted_state, "submitted_at", None), field_name="submitted_at")
    )
    if not started_at:
        logger.warning(
            "provenance_started_at_invalid attempt_id=%s action=finalize_failed",
            getattr(persisted_state, "attempt_id", None),
        )
        signals["provenance_status"] = "failed"
        signals["provenance_finalized"] = True
        signals["provenance_error"] = str(signals.get("provenance_error") or "invalid_provenance_started_at")
        signals["provenance_finalized_at"] = utc_now()
        persisted_state.signals = signals
        persisted_state.updated_at = utc_now()
        db.add(persisted_state)
        db.commit()
        return signals

    age_seconds = (utc_now_dt() - started_at).total_seconds()
    if age_seconds < PROVENANCE_FINALIZATION_TIMEOUT_SECONDS:
        return signals

    signals["provenance_status"] = "failed"
    signals["provenance_finalized"] = True
    signals["provenance_error"] = str(signals.get("provenance_error") or "analysis_timeout")
    signals["provenance_finalized_at"] = utc_now()
    persisted_state.signals = signals
    persisted_state.updated_at = utc_now()
    db.add(persisted_state)
    db.commit()
    logger.info(
        "provenance_timeout_reached attempt_id=%s age_seconds=%s",
        getattr(persisted_state, "attempt_id", None),
        int(age_seconds),
    )
    logger.info(
        "provenance_status_finalized attempt_id=%s status=%s",
        getattr(persisted_state, "attempt_id", None),
        "failed",
    )
    return signals


def _build_hybrid_reasoning_context(
    *,
    final_risk_level: str,
    session_intelligence: Dict[str, Any] | None,
    recommendation: str,
    why_this_score: str,
) -> Dict[str, Any]:
    intelligence = dict(session_intelligence or {})
    hybrid = dict(intelligence.get("hybrid_reasoning") or {})
    ml_advisory_level = str(hybrid.get("ml_advisory_level") or "").strip().upper() or None
    deterministic_risk_level = str(hybrid.get("deterministic_risk_level") or "").strip().upper() or None
    behavioral_correlation_strength = str(hybrid.get("behavioral_correlation_strength") or "").strip().upper() or None
    escalation_reason = str(hybrid.get("escalation_reason") or "").strip() or None

    summary_text = recommendation
    why_score_text = why_this_score

    if escalation_reason:
        summary_text = escalation_reason
        why_score_text = (
            f"{escalation_reason} {why_this_score}".strip()
            if why_this_score and why_this_score not in escalation_reason
            else escalation_reason
        )
    elif ml_advisory_level:
        summary_text = (
            "The final risk level reflects combined deterministic analysis, behavioral telemetry correlation, and ML-assisted scoring."
        )
        why_score_text = (
            f"ML-assisted assessment indicated {ml_advisory_level} concern while the final integrity risk settled at {final_risk_level} after deterministic review and behavioral correlation."
        )

    return {
        "ml_advisory_level": ml_advisory_level,
        "deterministic_risk_level": deterministic_risk_level,
        "behavioral_correlation_strength": behavioral_correlation_strength,
        "escalation_reason": escalation_reason,
        "summary_text": summary_text,
        "why_score_text": why_score_text,
    }


def _build_candidate_safe_demo_report(report: Dict[str, Any]) -> Dict[str, Any]:
    provenance = dict(report.get("provenance_analysis") or {})
    signals = ((report.get("signals") or {}) if isinstance(report.get("signals"), dict) else {}) or {}
    submitted_answers = list(signals.get("submitted_answers") or [])
    saved_provenance_status = str(signals.get("provenance_status") or "").strip().lower()
    provenance_ready = bool(
        provenance
        and (
            "summary" in provenance
            or "possible_reference_matches" in provenance
            or "external_similarity_likelihood" in provenance
        )
    )
    if provenance_ready:
        provenance_status = "ready"
    elif saved_provenance_status in {"processing", "ready", "unavailable", "failed"}:
        provenance_status = saved_provenance_status
    elif submitted_answers == []:
        provenance_status = "unavailable"
    else:
        provenance_status = "processing"
    possible_matches = [
        {
            "source_title": match.get("source_title"),
            "source_type": match.get("source_type"),
            "source_url": match.get("source_url"),
            "source_domain": match.get("source_domain"),
            "retrieved_from": match.get("retrieved_from"),
            "retrieval_source": match.get("retrieval_source"),
            "retrieval_confidence": round(safe_float(match.get("retrieval_confidence"), 0.0), 4) if match.get("retrieval_confidence") is not None else None,
            "retrieval_timestamp": match.get("retrieval_timestamp"),
            "content_snippet": match.get("content_snippet"),
            "question_id": match.get("question_id"),
            "question_title": match.get("question_title"),
            "similarity_percent": int(match.get("similarity_percent") or 0),
            "similarity_score": round(safe_float(match.get("similarity_score"), 0.0), 4),
            "likelihood": match.get("likelihood") or "LOW",
            "token_overlap": round(safe_float(match.get("token_overlap"), 0.0), 4),
            "phrase_overlap": round(safe_float(match.get("phrase_overlap"), 0.0), 4),
            "rare_term_overlap": round(safe_float(match.get("rare_term_overlap"), 0.0), 4),
            "chunk_similarity": round(safe_float(match.get("chunk_similarity"), 0.0), 4),
            "semantic_similarity": round(safe_float(match.get("semantic_similarity"), 0.0), 4) if match.get("semantic_similarity") is not None else None,
            "semantic_provider": match.get("semantic_provider"),
            "semantic_enabled": bool(match.get("semantic_enabled")),
            "confidence_label": match.get("confidence_label") or "Low confidence",
            "match_reason": match.get("match_reason") or "",
            "candidate_excerpt": match.get("candidate_excerpt") or "",
            "reference_excerpt": match.get("reference_excerpt") or "",
            "behavioral_correlation": list(match.get("behavioral_correlation") or []),
        }
        for match in list(provenance.get("possible_reference_matches") or [])[:3]
    ]
    provenance_summary = None
    if provenance:
        provenance_summary = {
            "external_similarity_likelihood": provenance.get("external_similarity_likelihood") or "LOW",
            "confidence_score": round(safe_float(provenance.get("confidence_score"), 0.0), 4),
            "summary": provenance.get("summary") or "No meaningful reference overlap was detected in submitted answers.",
            "behavioral_correlation": list(provenance.get("behavioral_correlation") or []),
            "possible_reference_matches": possible_matches,
            "matching_segment_preview": dict(provenance.get("matching_segment_preview") or {}),
            "evidence_title": provenance.get("evidence_title"),
            "reviewer_summary": provenance.get("reviewer_summary") or provenance.get("summary") or "",
            "limitations_note": provenance.get("limitations_note") or "",
            "web_retrieval_disclaimer": provenance.get("web_retrieval_disclaimer") or "",
            "generated_at": provenance.get("generated_at"),
            "retrieval_duration_ms": provenance.get("retrieval_duration_ms"),
            "provenance_status": provenance_status,
        }

    return {
        "attempt_id": report.get("attempt_id") or "",
        "candidate_name": report.get("candidate_name") or "Unknown Candidate",
        "candidate_email": report.get("candidate_email") or "No email available",
        "assessment_name": report.get("assessment_name") or "Assessment",
        "attempt_status": report.get("attempt_status") or "SUBMITTED",
        "final_decision": report.get("final_decision") or "Reviewer attention recommended",
        "submitted_at": report.get("submitted_at"),
        "started_at": report.get("started_at"),
        "latest_event_at": report.get("latest_event_at"),
        "event_count": int(report.get("event_count") or 0),
        "risk_level": ((report.get("final_risk_assessment") or {}).get("risk_level")) or "LOW",
        "risk_score": round(safe_float((report.get("final_risk_assessment") or {}).get("combined_score"), 0.0), 4),
        "confidence": round(safe_float((report.get("final_risk_assessment") or {}).get("confidence"), 0.0), 4),
        "strongest_reason": report.get("strongest_reason") or "Behavioral signals were reviewed in context after submission.",
        "summary_text": report.get("summary_text") or report.get("recommendation") or report.get("why_score_text") or "Behavioral signals were analyzed after assessment submission.",
        "why_score_text": report.get("why_score_text") or report.get("summary_text") or report.get("recommendation") or "Behavioral signals were analyzed after assessment submission.",
        "behavioral_summary": report.get("summary_text") or report.get("why_score_text") or report.get("recommendation") or "Behavioral signals were analyzed after assessment submission.",
        "hybrid_reasoning": dict(report.get("hybrid_reasoning") or {}),
        "most_suspicious_behaviors": list(report.get("investigation_insights") or [])[:6],
        "violation_summary": dict(report.get("violation_overview_counts") or {}),
        "evidence_items": list(report.get("evidence_items") or []),
        "answer_provenance": provenance_summary,
        "provenance_status": provenance_status,
        "provenance_ready": provenance_ready,
        "provenance_finalized": provenance_status in {"ready", "unavailable", "failed"},
        "privacy_note": "This summary reflects metadata-based integrity analysis only. It does not use webcam, microphone, screen recording, or clipboard contents.",
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


@app.get("/v1/debug/provenance-config")
def debug_provenance_config():
    if not _is_local_mode():
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "status": "success",
        "data": get_provenance_retrieval_diagnostics(),
    }


@app.get("/health/deep")
def health_deep():
    database = _database_health()
    redis = redis_health()
    overall = "ok" if database.get("reachable") and redis.get("reachable") else "degraded"
    counts = {"attempts": 0, "cases": 0, "events": 0}
    db = SessionLocal()
    try:
        counts = _startup_counts(db)
    finally:
        db.close()

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
        "counts": counts,
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
        features = result.get("features") or {}
        signals = result.get("signals") or {}
        evidence_items = normalize_evidence(
            events=batch.events,
            features=features,
            risk_score=score_value,
            risk_level=risk_level,
        )
        violation_overview = build_violation_overview_counts(events=batch.events, features=features)

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
        _upsert_attempt_log(
            db,
            attempt_id=batch.attempt_id,
            risk_level=risk_level,
            confidence=confidence_value,
            score=score_value,
            features=features,
            signals=signals,
            timestamp=snapshot_timestamp,
        )
        case_record = _ensure_case_for_attempt(db, batch.attempt_id)
        _upsert_attempt_state(
            db,
            attempt_id=batch.attempt_id,
            candidate_id=None,
            candidate_name=None,
            candidate_email=None,
            assessment_id=None,
            assessment_name=None,
            review_status=getattr(case_record, "status", None),
            latest_event_type=None,
            latest_event_at=snapshot_timestamp,
            event_count=len(batch.events),
            risk_level=risk_level,
            score=score_value,
            confidence=confidence_value,
            strongest_reason=reason,
            violation_overview=violation_overview,
            evidence_summary=evidence_items,
            risk_history=_serialize_risk_history_points(timeline_points),
            features=features,
            signals=signals,
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
    started_at = time.perf_counter()

    try:
        if not event.event_type:
            raise HTTPException(status_code=400, detail="event_type is required")

        received_at = utc_now()
        is_submission_event = _is_submission_event_type(event.event_type)
        persist_raw_event_started_at = time.perf_counter()
        raw_event_data = {
            "attempt_id": event.attempt_id,
            "candidate_id": event.candidate_id,
            "candidate_name": event.candidate_name,
            "candidate_email": event.candidate_email,
            "assessment_id": event.assessment_id,
            "assessment_name": event.assessment_name,
            "event_type": event.event_type,
            "payload": event.payload,
            "occurred_at": event.occurred_at,
            "received_at": received_at,
        }
        _with_retry_session(
            "event_ingest_raw_event",
            lambda db: (db.add(RawExamEvent(**raw_event_data)), db.commit()),
        )
        persist_raw_event_ms = round((time.perf_counter() - persist_raw_event_started_at) * 1000, 2)

        redis_event = {
            "event_type": event.event_type,
            "payload": event.payload,
            "occurred_at": event.occurred_at,
        }

        append_event(event.attempt_id, redis_event)
        scoring_events = get_events(event.attempt_id)

        if not scoring_events:
            scoring_events = _with_retry_session(
                "event_ingest_load_events",
                lambda db: [
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
                ],
                attempts=2,
            )

        scoring_started_at = time.perf_counter()
        result = run_scoring(scoring_events, event.attempt_id)
        scoring_ms = round((time.perf_counter() - scoring_started_at) * 1000, 2)
        score_value = safe_float(result.get("combined_score"), 0.0)
        confidence_value = safe_float(result.get("confidence"), 0.0)
        risk_level = normalize_risk_level(score_value)
        reason = build_reason(result)
        timeline_points = list(result.get("timeline_points") or [])
        latest_timeline_point = _last_timeline_point(result)
        snapshot_timestamp = latest_timeline_point.get("timestamp") or event.occurred_at or received_at
        features = result.get("features") or {}
        signals = result.get("signals") or {}
        evidence_items = normalize_evidence(
            events=scoring_events,
            features=features,
            risk_score=score_value,
            risk_level=risk_level,
        )
        violation_overview = build_violation_overview_counts(events=scoring_events, features=features)

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
            "strongest_reason": reason,
            "event_count": len(scoring_events),
            "updated_at": received_at,
            "session_intelligence": result.get("session_intelligence") or {},
            "features": features,
            "signals": signals,
        }

        persistence_started_at = time.perf_counter()
        def _persist_scoring_state(db):
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
                force=is_submission_event or _risk_history_gap_elapsed(latest_history, timestamp=snapshot_timestamp),
            )
            _upsert_attempt_log(
                db,
                attempt_id=event.attempt_id,
                risk_level=risk_level,
                confidence=confidence_value,
                score=score_value,
                features=features,
                signals=signals,
                timestamp=snapshot_timestamp,
            )
            case_record = _ensure_case_for_attempt(db, event.attempt_id)
            attempt_state = _upsert_attempt_state(
                db,
                attempt_id=event.attempt_id,
                candidate_id=event.candidate_id,
                candidate_name=event.candidate_name,
                candidate_email=event.candidate_email,
                assessment_id=event.assessment_id,
                assessment_name=event.assessment_name,
                review_status=getattr(case_record, "status", None),
                latest_event_type=event.event_type,
                latest_event_at=event.occurred_at or received_at,
                event_count=len(scoring_events),
                risk_level=risk_level,
                score=score_value,
                confidence=confidence_value,
                strongest_reason=reason,
                violation_overview=violation_overview,
                evidence_summary=evidence_items,
                risk_history=_serialize_risk_history_points(timeline_points),
                features=features,
                signals=signals,
            )
            db.commit()
            return {
                "attempt_status": getattr(attempt_state, "status", None),
                "submitted_at": getattr(attempt_state, "submitted_at", None),
                "case_id": getattr(case_record, "id", None),
                "case_status": getattr(case_record, "status", None),
            }

        persistence_result = _with_retry_session("event_ingest_persist_state", _persist_scoring_state)
        current_risk_state["attempt_status"] = persistence_result.get("attempt_status")
        current_risk_state["submitted_at"] = persistence_result.get("submitted_at")
        set_current_risk(event.attempt_id, current_risk_state)
        persistence_ms = round((time.perf_counter() - persistence_started_at) * 1000, 2)

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
            "attempt_status": persistence_result.get("attempt_status"),
            "submitted_at": persistence_result.get("submitted_at"),
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

        broadcast_started_at = time.perf_counter()
        websocket_payloads = [broadcast_payload]
        if is_submission_event:
            websocket_payloads.append({
                "type": "attempt_submitted",
                "attempt_id": event.attempt_id,
                "candidate_id": event.candidate_id,
                "candidate_name": event.candidate_name,
                "assessment_id": event.assessment_id,
                "assessment_name": event.assessment_name,
                "risk": risk_level,
                "combined_score": score_value,
                "confidence": confidence_value,
                "attempt_status": persistence_result.get("attempt_status"),
                "submitted_at": persistence_result.get("submitted_at"),
                "occurred_at": event.occurred_at or received_at,
            })
        if persistence_result.get("case_id") is not None:
            websocket_payloads.append({
                "type": "case_created_or_updated",
                "attempt_id": event.attempt_id,
                "case_id": persistence_result.get("case_id"),
                "status": persistence_result.get("case_status"),
                "risk": risk_level,
                "combined_score": score_value,
                "confidence": confidence_value,
                "updated_at": received_at,
            })

        await asyncio.gather(*(manager.broadcast(payload) for payload in websocket_payloads))
        broadcast_ms = round((time.perf_counter() - broadcast_started_at) * 1000, 2)
        total_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "ingest_event_timing attempt_id=%s event_type=%s total_ms=%.2f raw_event_ms=%.2f scoring_ms=%.2f persistence_ms=%.2f broadcast_ms=%.2f event_count=%s",
            event.attempt_id,
            event.event_type,
            total_ms,
            persist_raw_event_ms,
            scoring_ms,
            persistence_ms,
            broadcast_ms,
            len(scoring_events),
        )

        return {
            "status": "success",
            "message": "Event ingested into Redis + PostgreSQL and attempt re-scored",
            "attempt_id": event.attempt_id,
            "event_count": len(scoring_events),
            "current_risk": risk_level,
            "current_confidence": confidence_value,
            "current_score": score_value,
            "timings_ms": {
                "raw_event": persist_raw_event_ms,
                "scoring": scoring_ms,
                "persistence": persistence_ms,
                "broadcast": broadcast_ms,
                "total": total_ms,
            },
            "result": {
                **result,
                "risk": risk_level,
                "confidence": confidence_value,
                "combined_score": score_value,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to ingest event: {e}") from e


@app.post("/v1/attempts/{attempt_id}/answers")
def persist_submitted_answers(attempt_id: str, payload: SubmittedAnswersPayload):
    if attempt_id != payload.attempt_id:
        raise HTTPException(status_code=400, detail="Attempt id mismatch")

    answers = [
        {
            "question_id": item.question_id,
            "question_title": item.question_title,
            "section_id": item.section_id,
            "section_title": item.section_title,
            "assessment_type": item.assessment_type,
            "input_type": item.input_type,
            "answer_text": item.answer_text,
            "marked_for_review": bool(item.marked_for_review),
        }
        for item in payload.answers
        if str(item.answer_text or "").strip()
    ]
    answer_lengths = [len(str(item.get("answer_text") or "").strip()) for item in answers]
    assessment_name = payload.assessment_name or ""

    try:
        logger.info(
            "provenance_answers_received attempt_id=%s answers=%s assessment=%s lengths=%s",
            attempt_id,
            len(answers),
            assessment_name,
            ",".join(str(length) for length in answer_lengths[:12]),
        )
        logger.info(
            "answers_received_count attempt_id=%s count=%s answer_lengths=%s provenance_input_question_count=%s",
            attempt_id,
            len(answers),
            ",".join(str(length) for length in answer_lengths[:12]),
            len(answers),
        )

        def _persist_answers_state(db):
            attempt_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
            if attempt_state is None:
                attempt_state = AttemptState(attempt_id=attempt_id, updated_at=utc_now(), status="SUBMITTED")
                db.add(attempt_state)
            existing_signals = dict(getattr(attempt_state, "signals", None) or {})
            existing_signals["submitted_answers"] = answers
            existing_signals["provenance_status"] = "unavailable" if not answers else "processing"
            existing_signals["provenance_finalized"] = bool(not answers)
            existing_signals["provenance_started_at"] = utc_now() if answers else existing_signals.get("provenance_started_at")
            existing_signals.pop("provenance_error", None)
            existing_signals.pop("answer_provenance", None)
            existing_signals.pop("provenance_finalized_at", None)
            attempt_state.candidate_id = payload.candidate_id or attempt_state.candidate_id
            attempt_state.candidate_name = payload.candidate_name or attempt_state.candidate_name
            attempt_state.candidate_email = payload.candidate_email or attempt_state.candidate_email
            attempt_state.assessment_id = payload.assessment_id or attempt_state.assessment_id
            attempt_state.assessment_name = payload.assessment_name or attempt_state.assessment_name
            attempt_state.signals = existing_signals
            attempt_state.updated_at = utc_now()
            if not attempt_state.submitted_at:
                attempt_state.submitted_at = attempt_state.updated_at
            if attempt_state.status in {"ONGOING", "IN_PROGRESS", ""}:
                attempt_state.status = "SUBMITTED"
            db.commit()
            return attempt_state.assessment_name or assessment_name

        try:
            resolved_assessment_name = _with_retry_session("persist_submitted_answers_initial", _persist_answers_state)
        except OperationalError as exc:
            logger.error("sqlite_lock_retry_exhausted operation=%s attempt_id=%s", "persist_submitted_answers_initial", attempt_id)
            raise exc

        logger.info("persisted_answers_count attempt_id=%s count=%s", attempt_id, len(answers))

        if not answers:
            logger.info("provenance_status_finalized attempt_id=%s status=%s", attempt_id, "unavailable")
            logger.info("provenance_analysis_completed attempt_id=%s matches=0 likelihood=LOW confidence=0.00", attempt_id)
            return {
                "status": "success",
                "attempt_id": attempt_id,
                "data": None,
            }

        events = _with_retry_session(
            "persist_submitted_answers_load_events",
            lambda db: [
                {
                    "event_type": row.event_type,
                    "payload": row.payload or {},
                    "occurred_at": row.occurred_at,
                }
                for row in (
                    db.query(RawExamEvent)
                    .filter(RawExamEvent.attempt_id == attempt_id)
                    .order_by(RawExamEvent.occurred_at.asc())
                    .all()
                )
            ],
            attempts=2,
        )
        logger.info("provenance_analysis_started attempt_id=%s answer_count=%s", attempt_id, len(answers))
        try:
            provenance_result = analyze_answer_provenance(
                attempt_id=attempt_id,
                assessment_name=resolved_assessment_name or assessment_name,
                submitted_answers=answers,
                events=events,
            )
            logger.info(
                "provenance_analysis_completed attempt_id=%s matches=%s likelihood=%s confidence=%.2f",
                attempt_id,
                len(provenance_result.get("possible_reference_matches") or []),
                provenance_result.get("external_similarity_likelihood") or "LOW",
                safe_float(provenance_result.get("confidence_score"), 0.0),
            )

            def _persist_provenance_result(db):
                refreshed_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
                persisted_signals = dict(getattr(refreshed_state, "signals", None) or {})
                if persisted_signals.get("answer_provenance") and not provenance_result:
                    return persisted_signals
                persisted_signals["submitted_answers"] = answers
                persisted_signals["answer_provenance"] = provenance_result
                persisted_signals["provenance_status"] = "ready"
                persisted_signals["provenance_finalized"] = True
                persisted_signals["provenance_finalized_at"] = utc_now()
                persisted_signals.pop("provenance_error", None)
                refreshed_state.signals = persisted_signals
                refreshed_state.updated_at = utc_now()
                db.commit()
                return persisted_signals

            persisted_signals = _with_retry_session("persist_submitted_answers_provenance", _persist_provenance_result)
            logger.info("provenance_status_finalized attempt_id=%s status=%s", attempt_id, "ready")
            logger.info(
                "provenance_persisted attempt_id=%s signals_keys=%s",
                attempt_id,
                ",".join(sorted(persisted_signals.keys())),
            )
            return {
                "status": "success",
                "attempt_id": attempt_id,
                "data": provenance_result,
            }
        except Exception as exc:
            logger.exception(
                "provenance_analysis_failed attempt_id=%s answer_count=%s lengths=%s",
                attempt_id,
                len(answers),
                ",".join(str(length) for length in answer_lengths[:12]),
            )
            def _persist_failed_provenance(db):
                failure_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
                failure_signals = dict(getattr(failure_state, "signals", None) or {})
                if failure_signals.get("answer_provenance"):
                    return failure_signals
                failure_signals["submitted_answers"] = answers
                failure_signals["provenance_status"] = "failed"
                failure_signals["provenance_finalized"] = True
                failure_signals["provenance_error"] = "transient_analysis_failure"
                failure_signals["provenance_finalized_at"] = utc_now()
                failure_state.signals = failure_signals
                failure_state.updated_at = utc_now()
                db.commit()
                return failure_signals

            _with_retry_session("persist_submitted_answers_failed_state", _persist_failed_provenance)
            logger.info("provenance_status_finalized attempt_id=%s status=%s", attempt_id, "failed")
            return {
                "status": "failed",
                "attempt_id": attempt_id,
                "data": None,
                "message": "Submitted answers were stored, but provenance analysis could not be completed for this attempt.",
            }
    except HTTPException:
        raise
    except Exception as exc:
        if _is_sqlite_lock_error(exc):
            logger.error("sqlite_lock_retry_exhausted operation=%s attempt_id=%s", "persist_submitted_answers", attempt_id)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to persist submitted answers: {exc}") from exc


@app.get("/v1/live-risk/{attempt_id}")
def get_live_risk(attempt_id: str, _user=Depends(require_reviewer)):
    db = SessionLocal()
    try:
        cleanup_result = expire_stale_demo_attempts_if_due(db)
        if cleanup_result["updated_count"]:
            db.commit()
    finally:
        db.close()
    data = get_current_risk(attempt_id)

    if not data:
        db = SessionLocal()
        try:
            persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
            if persisted_state is not None:
                data = {
                    "attempt_id": attempt_id,
                    "candidate_id": persisted_state.candidate_id,
                    "candidate_name": persisted_state.candidate_name,
                    "candidate_email": persisted_state.candidate_email,
                    "assessment_id": persisted_state.assessment_id,
                    "assessment_name": persisted_state.assessment_name,
                    "risk": persisted_state.final_risk_level,
                    "confidence": safe_float(persisted_state.confidence, 0.0),
                    "combined_score": safe_float(persisted_state.final_risk_score, 0.0),
                    "explanation": persisted_state.strongest_reason,
                    "event_count": int(persisted_state.event_count or 0),
                    "updated_at": persisted_state.updated_at,
                    "attempt_status": persisted_state.status,
                    "submitted_at": persisted_state.submitted_at,
                    "features": persisted_state.features or {},
                    "signals": persisted_state.signals or {},
                }
            else:
                return {
                    "status": "not_found",
                    "attempt_id": attempt_id,
                    "data": None,
                }
        finally:
            db.close()

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
        persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
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
        if not data and persisted_state is not None:
            data = list(getattr(persisted_state, "risk_history", None) or [])

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


@app.get("/v1/reports/{attempt_id}/status")
def get_attempt_report_status(attempt_id: str):
    db = SessionLocal()
    try:
        cleanup_result = expire_stale_demo_attempts_if_due(db)
        if cleanup_result["updated_count"]:
            db.commit()
        report_url = f"/demo/report/{attempt_id}" if _is_public_demo_attempt_id(attempt_id) else f"/?attemptId={attempt_id}"
        persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
        if persisted_state is None:
            return {
                "status": "pending",
                "attempt_id": attempt_id,
                "data": {
                    "report_exists": False,
                    "provenance_ready": False,
                    "provenance_status": "processing",
                    "provenance_finalized": False,
                    "attempt_status": None,
                    "submitted_at": None,
                    "report_url": report_url,
                },
            }

        signals = _finalize_stale_provenance_state(db, persisted_state)
        provenance_result = dict(signals.get("answer_provenance") or {})
        provenance_status = str(signals.get("provenance_status") or "").strip().lower()
        submitted_answers = list(signals.get("submitted_answers") or [])
        provenance_ready = bool(
            provenance_result
            and (
                "summary" in provenance_result
                or "possible_reference_matches" in provenance_result
                or "external_similarity_likelihood" in provenance_result
            )
        )
        provenance_finalized = provenance_ready or bool(signals.get("provenance_finalized")) or provenance_status in {"unavailable", "failed"} or submitted_answers == []
        report_exists = bool(
            getattr(persisted_state, "final_risk_level", None)
            or getattr(persisted_state, "final_risk_score", None) is not None
            or getattr(persisted_state, "evidence_summary", None)
            or getattr(persisted_state, "violation_overview", None)
            or getattr(persisted_state, "submitted_at", None)
        )
        return {
            "status": "success",
            "attempt_id": attempt_id,
            "data": {
                "report_exists": report_exists,
                "provenance_ready": provenance_ready,
                "provenance_status": "ready" if provenance_ready else provenance_status or ("unavailable" if submitted_answers == [] else "processing"),
                "provenance_finalized": provenance_finalized,
                "attempt_status": getattr(persisted_state, "status", None),
                "submitted_at": getattr(persisted_state, "submitted_at", None),
                "report_url": report_url,
            },
        }
    except Exception as exc:
        traceback.print_exc()
        return {
            "status": "error",
            "attempt_id": attempt_id,
            "message": str(exc),
            "data": {
                    "report_exists": False,
                    "provenance_ready": False,
                    "provenance_status": "processing",
                    "provenance_finalized": False,
                    "attempt_status": None,
                    "submitted_at": None,
                    "report_url": report_url,
                },
            }
    finally:
        db.close()


@app.get("/v1/demo/attempts/{attempt_id}/report")
def get_public_demo_attempt_report(attempt_id: str):
    if not _is_public_demo_attempt_id(attempt_id):
        raise HTTPException(status_code=404, detail="Demo report not found")

    db = SessionLocal()
    try:
        cleanup_result = expire_stale_demo_attempts_if_due(db)
        if cleanup_result["updated_count"]:
            db.commit()
        persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()
        if persisted_state is None:
            return {
                "ready": False,
                "status": "processing",
                "attempt_id": attempt_id,
            }

        _finalize_stale_provenance_state(db, persisted_state)
        persisted_state = db.query(AttemptState).filter(AttemptState.attempt_id == attempt_id).first()

        report = _build_attempt_report(db, attempt_id)
        if not report:
            return {
                "ready": False,
                "status": "processing",
                "attempt_id": attempt_id,
            }

        provenance = dict(report.get("provenance_analysis") or {})
        provenance_ready = bool(
            provenance
            and (
                "summary" in provenance
                or "possible_reference_matches" in provenance
                or "external_similarity_likelihood" in provenance
            )
        )
        attempt_status = str(report.get("attempt_status") or getattr(persisted_state, "status", None) or "").upper()
        status_ready = attempt_status in {"SUBMITTED", "UNDER_REVIEW", "RESOLVED", "COMPLETED"}
        persisted_signals = dict(getattr(persisted_state, "signals", None) or {})
        submitted_answers = list((persisted_signals.get("submitted_answers") or []))
        saved_provenance_status = str(persisted_signals.get("provenance_status") or "").strip().lower()
        provenance_finalized = provenance_ready or bool(persisted_signals.get("provenance_finalized")) or saved_provenance_status in {"unavailable", "failed"} or submitted_answers == []
        report_ready = bool(
            status_ready
            and (
                report.get("submitted_at")
                or report.get("event_count")
                or report.get("final_risk_assessment")
            )
        )
        if not report_ready:
            return {
                "ready": False,
                "status": "processing",
                "attempt_id": attempt_id,
                "provenance_ready": provenance_ready,
                "provenance_status": "ready" if provenance_ready else saved_provenance_status or ("unavailable" if submitted_answers == [] else "processing"),
                "provenance_finalized": provenance_finalized,
                "attempt_status": attempt_status or None,
            }

        return {
            "ready": True,
            "status": "success",
            "attempt_id": attempt_id,
            "provenance_ready": provenance_ready,
            "provenance_finalized": provenance_finalized,
            "attempt_status": attempt_status or None,
            "data": _build_candidate_safe_demo_report(report),
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
    db = SessionLocal()
    try:
        data = _build_persisted_attempt_rows(db, recent_hours=None)
        return {
            "status": "success",
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
