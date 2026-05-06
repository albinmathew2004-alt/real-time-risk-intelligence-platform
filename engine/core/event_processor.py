from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .types import Event


def _parse_ts(ts: str) -> datetime:
    # Accept the Z suffix written by the synthetic generator.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


@dataclass(frozen=True)
class ProcessedEvents:
    attempt_id: str
    events: List[Event]
    occurred_at: List[datetime]


class EventProcessingError(ValueError):
    pass


def normalize_events(
    raw_events: Iterable[Dict[str, Any]],
    *,
    attempt_id: Optional[str] = None
) -> ProcessedEvents:
    """
    Normalize and validate an event batch.

    Improvements:
    - Allows missing attempt_id in events
    - Auto-injects attempt_id from request
    - Only throws error if explicit mismatch exists
    """

    events: List[Event] = []
    occurred: List[datetime] = []

    for ev in raw_events:
        if not isinstance(ev, dict):
            continue

        if "occurred_at" not in ev or "event_type" not in ev:
            continue

        # Parse timestamp safely
        try:
            t = _parse_ts(str(ev["occurred_at"]))
        except Exception:
            continue

        event_attempt_id = ev.get("attempt_id")

        # ✅ FIX 1: Only validate if BOTH exist
        if event_attempt_id is not None and attempt_id is not None:
            if str(event_attempt_id) != str(attempt_id):
                raise EventProcessingError("attempt_id mismatch in events")

        # ✅ FIX 2: Auto-fill missing attempt_id
        if event_attempt_id is None:
            ev["attempt_id"] = attempt_id

        events.append(ev)  # type: ignore[arg-type]
        occurred.append(t)

    # ❌ No valid events
    if not events:
        raise EventProcessingError("No valid events provided")

    # ✅ Determine attempt_id safely
    inferred_attempt_id = attempt_id or events[0].get("attempt_id")

    if not inferred_attempt_id:
        raise EventProcessingError("Missing attempt_id")

    inferred_attempt_id = str(inferred_attempt_id)

    # ✅ Stable sort
    order = sorted(
        range(len(events)),
        key=lambda i: (
            occurred[i],
            int(events[i].get("client_seq", 0) or 0)
        )
    )

    events_sorted = [events[i] for i in order]
    occurred_sorted = [occurred[i] for i in order]

    return ProcessedEvents(
        attempt_id=inferred_attempt_id,
        events=events_sorted,
        occurred_at=occurred_sorted,
    )