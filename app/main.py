from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
import json
import os
import traceback

from engine.core.scorer import run_scoring

app = FastAPI()


# =========================
# REQUEST MODEL
# =========================

class EventBatch(BaseModel):
    attempt_id: str
    events: List[Dict[str, Any]]


# =========================
# HEALTH CHECK
# =========================

@app.get("/")
def home():
    return {"message": "Server running"}


# =========================
# SCORING ENDPOINT
# =========================

@app.post("/v1/score")
def score(batch: EventBatch):
    try:
        return run_scoring(batch.events, batch.attempt_id)
    except Exception as e:
        traceback.print_exc()
        return {
            "error": str(e)
        }


# =========================
# LOGS ENDPOINT (NEW)
# =========================

@app.get("/v1/logs")
def get_logs():
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