from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import json
import os
import traceback

from engine.core.scorer import run_scoring
from engine.db.database import Base, engine, SessionLocal
from engine.db.models import RawExamEvent, RiskHistory

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
from app.models.user import User  # noqa: F401 (ensures users table is registered on startup)

from fastapi import Depends


app = FastAPI(
    title="Real-Time Risk Intelligence Platform",
    version="2.2.0",
)

app.include_router(auth_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created/verified")
    except Exception as e:
        print("⚠️ Database initialization failed:", e)


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
    try:
        result = run_scoring(batch.events, batch.attempt_id)

        await manager.broadcast({
            "type": "risk_update",
            "mode": "batch_score",
            "attempt_id": result.get("attempt_id"),
            "risk": result.get("risk"),
            "confidence": result.get("confidence"),
            "combined_score": result.get("combined_score"),
            "explanation": result.get("explanation"),
        })

        return result

    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


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

        current_risk_state = {
            "attempt_id": event.attempt_id,
            "candidate_id": event.candidate_id,
            "candidate_name": event.candidate_name,
            "candidate_email": event.candidate_email,
            "assessment_id": event.assessment_id,
            "assessment_name": event.assessment_name,
            "risk": result.get("risk"),
            "confidence": result.get("confidence"),
            "combined_score": result.get("combined_score"),
            "explanation": result.get("explanation"),
            "event_count": len(scoring_events),
            "updated_at": received_at,
        }

        set_current_risk(event.attempt_id, current_risk_state)

        risk_entry = RiskHistory(
            attempt_id=event.attempt_id,
            candidate_id=event.candidate_id,
            candidate_name=event.candidate_name,
            candidate_email=event.candidate_email,
            assessment_id=event.assessment_id,
            assessment_name=event.assessment_name,
            risk=result.get("risk"),
            confidence=float(result.get("confidence", 0.0) or 0.0),
            combined_score=float(result.get("combined_score", 0.0) or 0.0),
            reason=build_reason(result),
            timestamp=received_at,
        )

        db.add(risk_entry)
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
            "risk": result.get("risk"),
            "confidence": result.get("confidence"),
            "combined_score": result.get("combined_score"),
            "explanation": result.get("explanation"),
            "event_count": len(scoring_events),
            "timeline_point": {
                "risk": result.get("risk"),
                "combined_score": result.get("combined_score"),
                "reason": build_reason(result),
                "timestamp": received_at,
            },
        }

        await manager.broadcast(broadcast_payload)

        return {
            "status": "success",
            "message": "Event ingested into Redis + PostgreSQL and attempt re-scored",
            "attempt_id": event.attempt_id,
            "event_count": len(scoring_events),
            "current_risk": result.get("risk"),
            "current_confidence": result.get("confidence"),
            "current_score": result.get("combined_score"),
            "result": result,
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
                "confidence": h.confidence,
                "combined_score": h.combined_score,
                "reason": h.reason,
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