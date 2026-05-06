from typing import List
from datetime import datetime
from .types import Event, Features, FeatureConfig


# =========================
# TIME PARSER
# =========================
def parse_time(ts: str):
    return datetime.fromisoformat(ts.replace("Z", ""))


# =========================
# MAIN FEATURE BUILDER
# =========================
def build_features(events: List[Event], cfg: FeatureConfig) -> Features:

    paste_count = 0
    tab_hidden_count = 0
    idle_spike_count = 0

    last_idle_start = None

    # =========================
    # BASIC COUNTS
    # =========================
    for ev in events:
        etype = ev.get("event_type")
        payload = ev.get("payload", {})

        # Clipboard
        if etype == "clipboard" and payload.get("action") == "paste":
            paste_count += 1

        # Tab switch
        if etype == "visibility_change" and payload.get("state") == "hidden":
            tab_hidden_count += 1

        # Idle tracking
        if etype == "idle_state":
            if payload.get("state") == "idle":
                last_idle_start = ev.get("occurred_at")
            elif payload.get("state") == "active" and last_idle_start:
                idle_spike_count += 1
                last_idle_start = None

    # =========================
    # TIMING CALCULATION (🔥 FIX)
    # =========================
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

    # Average time per question
    if question_times:
        avg_time = sum(question_times) / len(question_times)
    else:
        avg_time = 0.0

    # Total attempt duration
    if events:
        start_time = parse_time(events[0]["occurred_at"])
        end_time = parse_time(events[-1]["occurred_at"])
        attempt_duration = (end_time - start_time).total_seconds()
    else:
        attempt_duration = 0.0

    # =========================
    # FINAL FEATURES
    # =========================
    return {
        "paste_count": paste_count,
        "tab_hidden_count": tab_hidden_count,
        "idle_spike_count": idle_spike_count,
        "time_per_question_mean_s": avg_time,
        "questions_seen": len(question_times),
        "attempt_duration_s": attempt_duration,
        "client_seq_gaps": 0,
        "has_submit_event": True,
    }