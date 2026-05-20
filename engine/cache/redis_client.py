import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


_redis_client: Optional[redis.Redis] = None
_redis_unavailable: bool = False

# In-memory fallback (dev-only). Key -> (value, expires_at)
_mem_events: Dict[str, Tuple[List[str], float]] = {}
_mem_kv: Dict[str, Tuple[str, float]] = {}


def _now() -> float:
    return time.time()


def _ttl_seconds() -> int:
    return 60 * 60 * 6


def _get_redis() -> Optional[redis.Redis]:
    global _redis_client, _redis_unavailable

    if _redis_unavailable:
        return None

    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    try:
        _redis_client.ping()
        return _redis_client
    except Exception:
        # Fail open to in-memory cache for local dev.
        _redis_unavailable = True
        return None


def redis_health() -> Dict[str, Any]:
    client = _get_redis()
    if client is not None:
        try:
            client.ping()
            return {
                "reachable": True,
                "mode": "redis",
                "url": REDIS_URL,
            }
        except Exception:
            pass

    return {
        "reachable": False,
        "mode": "in-memory-fallback" if _redis_unavailable else "unavailable",
        "url": REDIS_URL,
    }


# =========================
# ACTIVE SESSION EVENTS
# =========================

def append_event(attempt_id: str, event: dict):
    key = f"attempt:{attempt_id}:events"

    client = _get_redis()
    if client is not None:
        client.rpush(key, json.dumps(event))
        client.expire(key, _ttl_seconds())
        return

    # In-memory fallback
    items, _exp = _mem_events.get(key, ([], 0.0))
    items.append(json.dumps(event))
    _mem_events[key] = (items, _now() + _ttl_seconds())


def get_events(attempt_id: str):
    key = f"attempt:{attempt_id}:events"

    client = _get_redis()
    if client is not None:
        raw = client.lrange(key, 0, -1)
        return [json.loads(item) for item in raw]

    # In-memory fallback
    entry = _mem_events.get(key)
    if not entry:
        return []
    items, exp = entry
    if exp and exp < _now():
        _mem_events.pop(key, None)
        return []
    return [json.loads(item) for item in items]


# =========================
# LIVE CURRENT RISK
# =========================

def set_current_risk(attempt_id: str, risk_data: dict):
    key = f"attempt:{attempt_id}:current_risk"

    client = _get_redis()
    if client is not None:
        client.set(key, json.dumps(risk_data), ex=_ttl_seconds())
        return

    _mem_kv[key] = (json.dumps(risk_data), _now() + _ttl_seconds())


def get_current_risk(attempt_id: str):
    key = f"attempt:{attempt_id}:current_risk"

    client = _get_redis()
    if client is not None:
        data = client.get(key)
        if not data:
            return None
        return json.loads(data)

    entry = _mem_kv.get(key)
    if not entry:
        return None
    value, exp = entry
    if exp and exp < _now():
        _mem_kv.pop(key, None)
        return None
    return json.loads(value)
