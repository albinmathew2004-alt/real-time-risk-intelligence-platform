from typing import Any, Dict, List
from datetime import datetime

from .types import Event, Features, FeatureConfig


def parse_time(ts: str):
    return datetime.fromisoformat(ts.replace("Z", ""))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def _payload_number(payload: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in payload:
            return _safe_float(payload.get(key), default)
    return default


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float]) -> float:
    if len(values) <= 1:
        return 0.0
    avg = _mean(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return variance ** 0.5


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def build_features(events: List[Event], cfg: FeatureConfig) -> Features:
    paste_count = 0
    tab_hidden_count = 0
    idle_spike_count = 0
    typing_burst_count = 0
    rapid_typing_sequences = 0
    paste_without_typing = 0
    abnormal_pause_recovery = 0
    backspace_activity_count = 0
    typing_telemetry_event_count = 0

    last_idle_start = None
    active_typing_start = None
    recent_pause = None
    pending_pastes: List[Dict[str, Any]] = []
    pause_durations: List[float] = []
    burst_lengths: List[float] = []
    session_durations: List[float] = []

    for ev in events:
        etype = ev.get("event_type")
        payload = ev.get("payload", {}) or {}
        occurred_at = ev.get("occurred_at")
        event_time = parse_time(occurred_at) if occurred_at else None

        if etype == "clipboard" and payload.get("action") == "paste":
            paste_count += 1
            if event_time is not None:
                pending_pastes.append({"time": event_time, "recovered": False})

        if etype == "visibility_change" and payload.get("state") == "hidden":
            tab_hidden_count += 1

        if etype == "idle_state":
            if payload.get("state") == "idle":
                last_idle_start = ev.get("occurred_at")
            elif payload.get("state") == "active" and last_idle_start:
                idle_spike_count += 1
                last_idle_start = None

        if etype in {"typing_started", "typing_stopped", "typing_pause", "typing_burst", "backspace_activity"}:
            typing_telemetry_event_count += 1

        if etype == "typing_started":
            active_typing_start = event_time

        elif etype == "typing_pause":
            pause_s = _payload_number(payload, "duration_s", "pause_duration_s", "duration_seconds")
            if pause_s <= 0:
                pause_s = _payload_number(payload, "pause_duration_ms", "duration_ms") / 1000.0
            if pause_s > 0:
                pause_durations.append(pause_s)
                if event_time is not None:
                    recent_pause = {"time": event_time, "duration_s": pause_s}

        elif etype == "typing_burst":
            typing_burst_count += 1
            burst_length = _payload_number(payload, "burst_length", "keystroke_count", "count")
            interval_ms = _payload_number(payload, "interval_ms", "avg_interval_ms", "mean_interval_ms")
            duration_ms = _payload_number(payload, "duration_ms", "burst_duration_ms")

            if burst_length > 0:
                burst_lengths.append(burst_length)

            if (
                burst_length >= 12
                or (burst_length >= 8 and 0 < interval_ms <= 90)
                or (burst_length >= 8 and 0 < duration_ms <= 1500)
            ):
                rapid_typing_sequences += 1

            if event_time is not None:
                for pending in pending_pastes:
                    if pending["recovered"]:
                        continue
                    delta = (event_time - pending["time"]).total_seconds()
                    if 0 <= delta <= 8 and burst_length >= 3:
                        pending["recovered"] = True

                if recent_pause and recent_pause.get("duration_s", 0.0) >= 8:
                    delta_from_pause = (event_time - recent_pause["time"]).total_seconds()
                    if 0 <= delta_from_pause <= 20 and (burst_length >= 10 or (0 < interval_ms <= 85)):
                        abnormal_pause_recovery += 1
                        recent_pause = None

        elif etype == "backspace_activity":
            backspace_count = int(_payload_number(payload, "count", "backspace_count", default=1))
            backspace_activity_count += max(1, backspace_count)
            if event_time is not None:
                for pending in pending_pastes:
                    if pending["recovered"]:
                        continue
                    delta = (event_time - pending["time"]).total_seconds()
                    if 0 <= delta <= 8 and backspace_count >= 2:
                        pending["recovered"] = True

        elif etype == "typing_stopped":
            duration_s = _payload_number(payload, "session_duration_s")
            if duration_s <= 0:
                duration_s = _payload_number(payload, "session_duration_ms") / 1000.0
            if duration_s <= 0 and active_typing_start is not None and event_time is not None:
                duration_s = max(0.0, (event_time - active_typing_start).total_seconds())
            if duration_s > 0:
                session_durations.append(duration_s)
            active_typing_start = None

    paste_without_typing = sum(1 for pending in pending_pastes if not pending["recovered"])
    avg_pause_duration = _mean(pause_durations)
    avg_burst_length = _mean(burst_lengths)
    avg_session_duration = _mean(session_durations)
    pause_cv = (_std(pause_durations) / avg_pause_duration) if avg_pause_duration > 0 else 0.0
    session_cv = (_std(session_durations) / avg_session_duration) if avg_session_duration > 0 else 0.0

    typing_penalty = (
        min(0.26, rapid_typing_sequences * 0.12)
        + min(0.30, paste_without_typing * 0.18)
        + min(0.20, abnormal_pause_recovery * 0.10)
        + min(0.14, max(0.0, pause_cv - 0.5) * 0.18)
        + min(0.12, max(0.0, session_cv - 0.45) * 0.14)
    )
    typing_consistency_score = 1.0 if typing_telemetry_event_count == 0 else _clamp01(1.0 - typing_penalty)
    typing_behavior_anomaly_count = (
        rapid_typing_sequences
        + paste_without_typing
        + abnormal_pause_recovery
        + (1 if typing_telemetry_event_count and typing_consistency_score < 0.55 else 0)
    )

    question_times = []
    current_start = None

    for ev in events:
        if ev.get("event_type") == "question_view":
            action = ev.get("payload", {}).get("action")

            if action == "enter":
                current_start = parse_time(ev["occurred_at"])

            elif action == "leave" and current_start:
                end = parse_time(ev["occurred_at"])
                duration = (end - current_start).total_seconds()

                if duration > 0:
                    question_times.append(duration)

                current_start = None

    avg_time = (sum(question_times) / len(question_times)) if question_times else 0.0

    if events:
        start_time = parse_time(events[0]["occurred_at"])
        end_time = parse_time(events[-1]["occurred_at"])
        attempt_duration = (end_time - start_time).total_seconds()
    else:
        attempt_duration = 0.0

    return {
        "paste_count": paste_count,
        "tab_hidden_count": tab_hidden_count,
        "idle_spike_count": idle_spike_count,
        "time_per_question_mean_s": avg_time,
        "questions_seen": len(question_times),
        "attempt_duration_s": attempt_duration,
        "typing_burst_count": typing_burst_count,
        "avg_pause_duration": round(avg_pause_duration, 3),
        "rapid_typing_sequences": rapid_typing_sequences,
        "paste_without_typing": paste_without_typing,
        "abnormal_pause_recovery": abnormal_pause_recovery,
        "typing_consistency_score": round(typing_consistency_score, 4),
        "typing_behavior_anomaly_count": int(typing_behavior_anomaly_count),
        "typing_telemetry_event_count": typing_telemetry_event_count,
        "backspace_activity_count": backspace_activity_count,
        "avg_typing_burst_length": round(avg_burst_length, 2),
        "client_seq_gaps": 0,
        "has_submit_event": True,
    }
