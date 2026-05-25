from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from engine.cache.redis_client import get_current_risk, set_current_risk
from engine.db.models import AttemptState


DEMO_EXPIRED_STATUS = "EXPIRED"
DEMO_ABANDONED_STATUS = "ABANDONED"
DEMO_EXPIRED_MESSAGE = "Candidate exited before final submission."
DEMO_STALE_TIMEOUT_MINUTES = int(os.getenv("DEMO_STALE_TIMEOUT_MINUTES", "45"))
DEMO_ATTEMPT_PREFIXES = tuple(
    prefix.strip()
    for prefix in os.getenv("DEMO_ATTEMPT_PREFIXES", "demo_,demo_public_,demo_dataset_").split(",")
    if prefix.strip()
)
SUBMISSION_EVENT_TYPES = {"exam_submitted", "assessment_submitted", "submit", "completed"}
ACTIVE_ATTEMPT_STATUSES = {"ONGOING", "IN_PROGRESS"}
TERMINAL_ATTEMPT_STATUSES = {"SUBMITTED", "UNDER_REVIEW", "RESOLVED", "COMPLETED", DEMO_EXPIRED_STATUS, DEMO_ABANDONED_STATUS}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
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


def is_demo_attempt_id(attempt_id: str | None, prefixes: Iterable[str] | None = None) -> bool:
    value = str(attempt_id or "").strip().lower()
    if not value:
        return False
    active_prefixes = tuple(prefixes or DEMO_ATTEMPT_PREFIXES)
    return any(value.startswith(prefix.lower()) for prefix in active_prefixes)


def expire_stale_demo_attempts(
    db,
    *,
    timeout_minutes: int | None = None,
    prefixes: Iterable[str] | None = None,
) -> dict[str, Any]:
    effective_timeout = max(1, int(timeout_minutes or DEMO_STALE_TIMEOUT_MINUTES))
    cutoff = _utcnow() - timedelta(minutes=effective_timeout)
    updated_attempt_ids: list[str] = []

    states = (
        db.query(AttemptState)
        .filter(AttemptState.status.in_(sorted(ACTIVE_ATTEMPT_STATUSES)))
        .all()
    )

    for state in states:
        attempt_id = str(getattr(state, "attempt_id", "") or "").strip()
        if not is_demo_attempt_id(attempt_id, prefixes):
            continue

        latest_event_type = str(getattr(state, "latest_event_type", "") or "").strip().lower()
        if latest_event_type in SUBMISSION_EVENT_TYPES or getattr(state, "submitted_at", None):
            continue

        latest_activity = (
            _parse_iso(getattr(state, "latest_event_at", None))
            or _parse_iso(getattr(state, "updated_at", None))
        )
        if latest_activity is None or latest_activity >= cutoff:
            continue

        state.status = DEMO_EXPIRED_STATUS
        if not getattr(state, "strongest_reason", None):
            state.strongest_reason = DEMO_EXPIRED_MESSAGE
        state.signals = {
            **(getattr(state, "signals", None) or {}),
            "attempt_status_note": DEMO_EXPIRED_MESSAGE,
        }

        cached_state = get_current_risk(attempt_id) or {}
        if cached_state:
            cached_state["attempt_status"] = DEMO_EXPIRED_STATUS
            cached_state["attempt_status_note"] = DEMO_EXPIRED_MESSAGE
            set_current_risk(attempt_id, cached_state)

        updated_attempt_ids.append(attempt_id)

    return {
        "updated_count": len(updated_attempt_ids),
        "updated_attempt_ids": updated_attempt_ids,
        "timeout_minutes": effective_timeout,
        "status": DEMO_EXPIRED_STATUS,
        "message": DEMO_EXPIRED_MESSAGE,
    }
