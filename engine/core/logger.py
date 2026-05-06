import json
import os
from datetime import datetime

LOG_FILE = "logs/attempt_logs.jsonl"


def log_attempt(result, features):
    """
    Append scoring result + features to a JSONL file
    """

    os.makedirs("logs", exist_ok=True)

    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "attempt_id": result.attempt_id,
        "risk": result.risk,
        "confidence": result.confidence,
        "confidence_score": result.confidence_score,
        "combined_score": result.combined_score,
        "features": features,
        "signals": result.signals,
    }

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")