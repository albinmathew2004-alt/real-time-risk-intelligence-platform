from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import traceback

from engine.core.scorer import run_scoring
from engine.db.database import Base, engine, SessionLocal, ensure_demo_schema
from engine.db.models import AttemptLog, InvestigationCase, RawExamEvent, RiskHistory

from engine.cache.redis_client import (
    append_event,
    get_events,
    set_current_risk,
    get_current_risk,
)

from app.websocket_manager import manager

# Auth (Phase 1)
from app.auth.routes import router as auth_router
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
from app.services.report_summary_service import build_report_summary

from fastapi import Depends

RISK_HISTORY_SCORE_EPSILON = 0.01
RISK_HISTORY_CONFIDENCE_EPSILON = 0.05
ATTEMPT_LOG_FILE = Path("logs/attempt_logs.jsonl")


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
    extra = [origin.strip() for origin in configured.split(",") if origin.strip()]
    seen = set()
    merged = []
    for origin in defaults + extra:
        if origin not in seen:
            seen.add(origin)
            merged.append(origin)
    return merged


app = FastAPI(
    title="Real-Time Risk Intelligence Platform",
    version="2.2.0",
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
        Base.metadata.create_all(bind=engine)
        ensure_demo_schema()
        print("[OK] Database tables created/verified")
    except Exception as e:
        print("[WARN] Database initialization failed:", e)


class EventBatch(BaseModel):
    attempt_id: str
    events: List[Dict[str, Any]]


class ExamEventIngest(BaseModel):
    attempt_id: str

    candidate_id: Optional[str] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None

    assessment_id: Optional[str] = None
    assessment_name: Optional[str] = None

    event_type: str
    payload: Dict[str, Any] = {}
    occurred_at: str


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def build_reason(result: Dict[str, Any]) -> str:
    risk = result.get("risk", "LOW")
    explanation = result.get("explanation", "")

    if isinstance(explanation, str) and explanation.strip():
        return explanation[:500]

    if risk == "HIGH":
        return "Risk escalated to HIGH based on strong behavioral evidence."

    if risk == "MEDIUM":
        return "Risk escalated to MEDIUM based on suspicious behavioral signals."

    return "Risk remains LOW. No major suspicious behavior detected."


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
    if previous_reason != reason:
        return True
    return False


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
) -> Optional[RiskHistory]:
    latest_history = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .first()
    )
    if not _risk_history_changed(
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
    if value == "CONFIRMED_RISK":
        return 1
    if value == "UNDER_INVESTIGATION":
        return 2
    if value == "TRIAGED":
        return 3
    if value == "NEW":
        return 4
    if value in {"CLEARED", "FALSE_POSITIVE"}:
        return 5
    if value == "CLOSED":
        return 6
    return 7


def _queue_status(case_record: Optional[InvestigationCase]) -> str:
    status = getattr(case_record, "status", None) or "NEW"
    if status in {"CLEARED", "FALSE_POSITIVE"}:
        return "RESOLVED"
    return status


def _resolved_case_status(status: str) -> bool:
    return status in {"RESOLVED", "CLEARED", "FALSE_POSITIVE", "CLOSED"}


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


def _build_dashboard_summary(db) -> Dict[str, Any]:
    _sync_cases_from_attempts(db)
    logs = _load_attempt_logs()
    latest_attempts = _dedupe_latest_attempt_rows(logs)
    latest_event_by_attempt = _latest_event_metadata(db)
    cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
    case_by_attempt = {case.attempt_id: case for case in cases if case.attempt_id}

    total_attempts = len(latest_attempts)
    active_sessions = sum(
        1
        for row in latest_attempts
        if not bool((row.get("features") or {}).get("has_submit_event"))
    )
    high_risk_count = sum(1 for row in latest_attempts if normalize_risk_level(row.get("combined_score")) == "HIGH")
    medium_risk_count = sum(1 for row in latest_attempts if normalize_risk_level(row.get("combined_score")) == "MEDIUM")
    low_risk_count = sum(1 for row in latest_attempts if normalize_risk_level(row.get("combined_score")) == "LOW")
    unresolved_cases = [
        case for case in cases
        if getattr(case, "status", None) not in {"CLEARED", "FALSE_POSITIVE", "CLOSED"}
    ]
    needs_review_count = len(unresolved_cases)
    escalated_count = sum(1 for case in cases if getattr(case, "status", None) == "ESCALATED")
    avg_confidence = round(
        sum(safe_float(row.get("confidence"), 0.0) for row in latest_attempts) / max(1, total_attempts),
        4,
    )

    latest_log_time = max(
        (parse_timestamp(row.get("timestamp")) for row in logs if row.get("timestamp")),
        default=None,
    )
    if latest_log_time is not None:
        window_start = latest_log_time.timestamp() - 300
        live_event_rate = max(
            0,
            round(
                sum(
                    1
                    for row in logs
                    if (parse_timestamp(row.get("timestamp")) or latest_log_time).timestamp() >= window_start
                ) / 5
            ),
        )
    else:
        live_event_rate = 0

    recent_risk_feed = []
    for row in latest_attempts[:5]:
        event_meta = latest_event_by_attempt.get(row.get("attempt_id"))
        risk_level = normalize_risk_level(row.get("combined_score"))
        recent_risk_feed.append({
            "attempt_id": row.get("attempt_id"),
            "candidate_name": row.get("candidate_name") or getattr(event_meta, "candidate_name", None),
            "candidate_email": row.get("candidate_email") or getattr(event_meta, "candidate_email", None),
            "timestamp": row.get("timestamp") or getattr(event_meta, "received_at", None) or getattr(event_meta, "occurred_at", None),
            "risk_level": risk_level,
            "risk": risk_level,
            "score": round(safe_float(row.get("combined_score"), 0.0), 4),
            "confidence": round(safe_float(row.get("confidence"), 0.0), 4),
            "attempt_summary": build_reason({
                "risk": risk_level,
                "explanation": row.get("explanation"),
            }),
            "latest_event_type": getattr(event_meta, "event_type", None),
        })

    cases_needing_review: List[Dict[str, Any]] = []
    candidate_rows = []
    for row in latest_attempts:
        attempt_id = row.get("attempt_id")
        if not attempt_id:
            continue
        case_record = case_by_attempt.get(attempt_id)
        queue_status = _queue_status(case_record)
        if _resolved_case_status(queue_status):
            continue
        event_meta = latest_event_by_attempt.get(attempt_id)
        candidate_rows.append({
            "attempt_id": attempt_id,
            "candidate_name": row.get("candidate_name") or getattr(event_meta, "candidate_name", None),
            "candidate_email": row.get("candidate_email") or getattr(event_meta, "candidate_email", None),
            "assessment_name": row.get("assessment_name") or getattr(event_meta, "assessment_name", None),
            "risk_level": normalize_risk_level(row.get("combined_score")),
            "risk": normalize_risk_level(row.get("combined_score")),
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
        "clipboard_copy_paste": sum(int(safe_float((row.get("features") or {}).get("paste_count"), 0.0)) for row in latest_attempts),
        "tab_switch_events": sum(int(safe_float((row.get("features") or {}).get("tab_hidden_count"), 0.0)) for row in latest_attempts),
        "idle_time_spikes": sum(int(safe_float((row.get("features") or {}).get("idle_spike_count"), 0.0)) for row in latest_attempts),
        "rapid_answer_bursts": sum(1 for row in latest_attempts if safe_float((row.get("features") or {}).get("time_per_question_mean_s"), 0.0) > 0 and safe_float((row.get("features") or {}).get("time_per_question_mean_s"), 0.0) <= 15),
        "focus_blur_events": sum(int(safe_float((row.get("features") or {}).get("tab_hidden_count"), 0.0)) for row in latest_attempts),
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
def get_dashboard_summary(_user=Depends(require_reviewer)):
    db = SessionLocal()
    try:
        data = _build_dashboard_summary(db)
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
    runtime_risk = current_risk
    if not runtime_risk and scoring_events:
        scoring_result = run_scoring(scoring_events, attempt_id)
        runtime_risk = {
            "attempt_id": attempt_id,
            "risk": scoring_result.get("risk"),
            "confidence": scoring_result.get("confidence"),
            "combined_score": scoring_result.get("combined_score"),
            "explanation": scoring_result.get("explanation"),
            "event_count": len(scoring_events),
        }

    latest_history = history[-1] if history else None
    features = getattr(latest_attempt_log, "features", None) or {}
    risk_score = (
        current_risk.get("combined_score")
        if current_risk.get("combined_score") is not None
        else getattr(latest_history, "combined_score", None)
        if latest_history is not None
        else getattr(latest_attempt_log, "combined_score", None)
    )
    confidence = (
        current_risk.get("confidence")
        if current_risk.get("confidence") is not None
        else getattr(latest_history, "confidence", None)
        if latest_history is not None
        else getattr(latest_attempt_log, "confidence", None)
    )
    risk_score = safe_float(risk_score, 0.0)
    confidence = safe_float(confidence, 0.0)
    risk_level = normalize_risk_level(risk_score)

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
    summary = build_report_summary(
        risk_score=risk_score,
        confidence=confidence,
        evidence_items=evidence_items,
    )
    overview_counts = build_violation_overview_counts(events=event_payloads, features=features)

    metadata_source = current_risk if current_risk else {}
    latest_event = events[-1] if events else None
    first_event = events[0] if events else None

    return {
        **summary,
        "attempt_id": attempt_id,
        "event_count": len(events),
        "evidence_items": evidence_items,
        "violation_overview_counts": overview_counts,
        "candidate_name": metadata_source.get("candidate_name")
        or getattr(latest_history, "candidate_name", None)
        or getattr(latest_event, "candidate_name", None),
        "candidate_email": metadata_source.get("candidate_email")
        or getattr(latest_history, "candidate_email", None)
        or getattr(latest_event, "candidate_email", None),
        "assessment_name": metadata_source.get("assessment_name")
        or getattr(latest_history, "assessment_name", None)
        or getattr(latest_event, "assessment_name", None),
        "latest_event_at": metadata_source.get("updated_at")
        or getattr(latest_history, "timestamp", None)
        or getattr(latest_event, "occurred_at", None),
        "started_at": getattr(first_event, "occurred_at", None),
        "raw_explanation": metadata_source.get("explanation")
        or getattr(latest_history, "reason", None),
    }


@app.get("/")
def home():
    return {
        "message": "Server running",
        "version": "2.2.0",
        "websocket": "/ws/risk",
        "progressive_ingestion": "/v1/events/ingest",
        "live_risk": "/v1/live-risk/{attempt_id}",
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
            timestamp=utc_now(),
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
        db.commit()

        redis_event = {
            "event_type": event.event_type,
            "payload": event.payload,
            "occurred_at": event.occurred_at,
        }

        append_event(event.attempt_id, redis_event)
        scoring_events = get_events(event.attempt_id)

        if not scoring_events:
            all_events = (
                db.query(RawExamEvent)
                .filter(RawExamEvent.attempt_id == event.attempt_id)
                .order_by(RawExamEvent.occurred_at.asc())
                .all()
            )
            scoring_events = db_events_to_scoring_events(all_events)

        result = run_scoring(scoring_events, event.attempt_id)
        score_value = safe_float(result.get("combined_score"), 0.0)
        confidence_value = safe_float(result.get("confidence"), 0.0)
        risk_level = normalize_risk_level(score_value)
        reason = build_reason(result)

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
        }

        set_current_risk(event.attempt_id, current_risk_state)

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
            timestamp=received_at,
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
                "timestamp": received_at,
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

    except Exception as e:
        db.rollback()
        traceback.print_exc()
        return {
            "status": "error",
            "message": str(e),
        }

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
