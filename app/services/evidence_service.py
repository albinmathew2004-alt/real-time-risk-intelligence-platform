from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


LOW_RISK_MAX = 0.40
HIGH_RISK_MIN = 0.70

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

TAB_SWITCHING = "TAB_SWITCHING"
CLIPBOARD_ACTIVITY = "CLIPBOARD_ACTIVITY"
FOCUS_BLUR = "FOCUS_BLUR"
IDLE_SPIKE = "IDLE_SPIKE"
RAPID_ANSWER_BURST = "RAPID_ANSWER_BURST"
SUSPICIOUS_SEQUENCE = "SUSPICIOUS_SEQUENCE"
HIGH_RISK_SCORE = "HIGH_RISK_SCORE"


def normalize_risk_level(score: Any) -> str:
    numeric = safe_float(score)
    if numeric >= HIGH_RISK_MIN:
        return HIGH
    if numeric >= LOW_RISK_MAX:
        return MEDIUM
    return LOW


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def format_duration(seconds: Any) -> str:
    total = int(max(0, safe_float(seconds, 0.0)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def find_nested_value(input_value: Any, keys: Iterable[str]) -> Any:
    key_set = set(keys)
    if input_value is None:
        return None
    if isinstance(input_value, list):
        for item in input_value:
            found = find_nested_value(item, key_set)
            if found not in (None, ""):
                return found
        return None
    if not isinstance(input_value, dict):
        return None

    for key, value in input_value.items():
        if key in key_set and value not in (None, ""):
            return value
    for value in input_value.values():
        found = find_nested_value(value, key_set)
        if found not in (None, ""):
            return found
    return None


def _severity_from_count(count: int, medium_threshold: int, high_threshold: int) -> str:
    if count >= high_threshold:
        return HIGH
    if count >= medium_threshold:
        return MEDIUM
    return LOW


def _count_event_type(events: List[Dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


def _matching_event_times(events: List[Dict[str, Any]], predicate) -> List[str]:
    return [str(event.get("occurred_at")) for event in events if predicate(event) and event.get("occurred_at")]


def _first_last(times: List[str]) -> tuple[Optional[str], Optional[str]]:
    if not times:
        return None, None
    return times[0], times[-1]


def _visibility_hidden(event: Dict[str, Any]) -> bool:
    if event.get("event_type") != "visibility_change":
        return False
    state = str(find_nested_value(event.get("payload"), ["state", "visibility_state", "visibility"]) or "").lower()
    return "hidden" in state or "blur" in state


def _focus_blur(event: Dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    return event_type in {"window_blur", "blur"} or _visibility_hidden(event)


def _idle_event_seconds(event: Dict[str, Any]) -> float:
    numeric = safe_float(
        find_nested_value(
            event.get("payload"),
            ["duration_s", "idle_duration_s", "idle_seconds", "seconds", "gap_s"],
        ),
        0.0,
    )
    return max(0.0, numeric)


def _collect_answer_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [event for event in events if event.get("event_type") == "question_answer"]


def _rapid_answer_burst_metrics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    answers = _collect_answer_events(events)
    burst_count = 0
    burst_times: List[str] = []

    for index in range(1, len(answers)):
        previous = parse_datetime(answers[index - 1].get("occurred_at"))
        current = parse_datetime(answers[index].get("occurred_at"))
        if previous is None or current is None:
            continue
        delta_seconds = (current - previous).total_seconds()
        if 0 <= delta_seconds <= 15:
            burst_count += 1
            burst_times.append(str(answers[index].get("occurred_at")))

    first_seen, last_seen = _first_last(burst_times)
    return {
        "count": burst_count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "related_event_count": len(answers),
    }


def _suspicious_sequence_metrics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = 0
    matched_times: List[str] = []

    for index in range(len(events) - 2):
        window_events = events[index:index + 3]
        event_types = [event.get("event_type") for event in window_events]
        has_clipboard = "clipboard" in event_types
        has_focus_loss = any(_focus_blur(event) for event in window_events)
        has_answer = "question_answer" in event_types
        if (has_clipboard and has_answer) or (has_focus_loss and has_answer):
            count += 1
            final_time = str(window_events[-1].get("occurred_at")) if window_events[-1].get("occurred_at") else None
            if final_time:
                matched_times.append(final_time)

    first_seen, last_seen = _first_last(matched_times)
    return {
        "count": count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "related_event_count": min(len(events), count * 3) if count else 0,
    }


def _build_item(
    *,
    signal_type: str,
    title: str,
    severity: str,
    count: int,
    explanation: str,
    first_seen: Optional[str],
    last_seen: Optional[str],
    related_event_count: int,
) -> Dict[str, Any]:
    return {
        "signal_type": signal_type,
        "title": title,
        "severity": severity,
        "count": count,
        "explanation": explanation,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "related_event_count": related_event_count,
    }


def normalize_evidence(
    *,
    events: List[Dict[str, Any]],
    features: Optional[Dict[str, Any]],
    risk_score: Any,
    risk_level: Optional[str] = None,
) -> List[Dict[str, Any]]:
    features = features or {}
    event_count = len(events)
    normalized_risk = risk_level or normalize_risk_level(risk_score)
    items: List[Dict[str, Any]] = []

    clipboard_count = int(safe_float(features.get("paste_count"), _count_event_type(events, "clipboard")))
    clipboard_times = _matching_event_times(events, lambda event: event.get("event_type") == "clipboard")
    if clipboard_count > 0:
        first_seen, last_seen = _first_last(clipboard_times)
        severity = _severity_from_count(clipboard_count, medium_threshold=2, high_threshold=5)
        items.append(
            _build_item(
                signal_type=CLIPBOARD_ACTIVITY,
                title="Clipboard Activity",
                severity=severity,
                count=clipboard_count,
                explanation=f"Clipboard metadata was captured {clipboard_count} time{'s' if clipboard_count != 1 else ''} during the attempt.",
                first_seen=first_seen,
                last_seen=last_seen,
                related_event_count=max(clipboard_count, len(clipboard_times)),
            )
        )

    tab_switch_count = int(safe_float(features.get("tab_hidden_count"), sum(1 for event in events if _visibility_hidden(event))))
    tab_switch_times = _matching_event_times(events, _visibility_hidden)
    if tab_switch_count > 0:
        first_seen, last_seen = _first_last(tab_switch_times)
        severity = _severity_from_count(tab_switch_count, medium_threshold=2, high_threshold=6)
        items.append(
            _build_item(
                signal_type=TAB_SWITCHING,
                title="Tab Switching",
                severity=severity,
                count=tab_switch_count,
                explanation=f"The assessment lost tab visibility {tab_switch_count} time{'s' if tab_switch_count != 1 else ''}.",
                first_seen=first_seen,
                last_seen=last_seen,
                related_event_count=max(tab_switch_count, len(tab_switch_times)),
            )
        )

    blur_times = _matching_event_times(events, _focus_blur)
    focus_blur_count = len(blur_times)
    if focus_blur_count > 0:
        first_seen, last_seen = _first_last(blur_times)
        severity = _severity_from_count(focus_blur_count, medium_threshold=3, high_threshold=6)
        items.append(
            _build_item(
                signal_type=FOCUS_BLUR,
                title="Focus / Blur Events",
                severity=severity,
                count=focus_blur_count,
                explanation=f"Window focus changed or blurred {focus_blur_count} time{'s' if focus_blur_count != 1 else ''} during the attempt.",
                first_seen=first_seen,
                last_seen=last_seen,
                related_event_count=focus_blur_count,
            )
        )

    idle_events = [event for event in events if event.get("event_type") == "idle_state"]
    idle_spike_count = int(safe_float(features.get("idle_spike_count"), len(idle_events)))
    idle_max_seconds = max(
        [safe_float(features.get("idle_max_duration_s"), 0.0), *[_idle_event_seconds(event) for event in idle_events]],
        default=0.0,
    )
    idle_times = [str(event.get("occurred_at")) for event in idle_events if event.get("occurred_at")]
    if idle_spike_count > 0 or idle_max_seconds > 0:
        first_seen, last_seen = _first_last(idle_times)
        severity = HIGH if idle_max_seconds >= 300 else MEDIUM if idle_max_seconds >= 180 or idle_spike_count >= 2 else LOW
        items.append(
            _build_item(
                signal_type=IDLE_SPIKE,
                title="Idle Spike",
                severity=severity,
                count=max(idle_spike_count, len(idle_events)),
                explanation=(
                    f"Idle telemetry recorded {max(idle_spike_count, len(idle_events))} spike"
                    f"{'s' if max(idle_spike_count, len(idle_events)) != 1 else ''}; longest observed gap was {format_duration(idle_max_seconds)}."
                ),
                first_seen=first_seen,
                last_seen=last_seen,
                related_event_count=len(idle_events),
            )
        )

    burst_metrics = _rapid_answer_burst_metrics(events)
    if burst_metrics["count"] > 0:
        severity = _severity_from_count(int(burst_metrics["count"]), medium_threshold=2, high_threshold=5)
        items.append(
            _build_item(
                signal_type=RAPID_ANSWER_BURST,
                title="Rapid Answer Burst",
                severity=severity,
                count=int(burst_metrics["count"]),
                explanation=f"Answer submissions arrived in quick succession {burst_metrics['count']} time{'s' if burst_metrics['count'] != 1 else ''}.",
                first_seen=burst_metrics["first_seen"],
                last_seen=burst_metrics["last_seen"],
                related_event_count=int(burst_metrics["related_event_count"]),
            )
        )

    sequence_metrics = _suspicious_sequence_metrics(events)
    if sequence_metrics["count"] > 0:
        severity = _severity_from_count(int(sequence_metrics["count"]), medium_threshold=1, high_threshold=3)
        items.append(
            _build_item(
                signal_type=SUSPICIOUS_SEQUENCE,
                title="Suspicious Sequence",
                severity=severity,
                count=int(sequence_metrics["count"]),
                explanation=(
                    "Clipboard or focus-loss activity was followed by answer submission within short event sequences "
                    f"{sequence_metrics['count']} time{'s' if sequence_metrics['count'] != 1 else ''}."
                ),
                first_seen=sequence_metrics["first_seen"],
                last_seen=sequence_metrics["last_seen"],
                related_event_count=int(sequence_metrics["related_event_count"]),
            )
        )

    if normalized_risk == HIGH:
        overall_last_seen = None
        for value in reversed([event.get("occurred_at") for event in events]):
            if value:
                overall_last_seen = str(value)
                break
        items.append(
            _build_item(
                signal_type=HIGH_RISK_SCORE,
                title="High Risk Score",
                severity=HIGH,
                count=1,
                explanation=f"The combined risk score reached {safe_float(risk_score):.2f}, which falls in the HIGH risk band.",
                first_seen=overall_last_seen,
                last_seen=overall_last_seen,
                related_event_count=event_count,
            )
        )

    items.sort(
        key=lambda item: (
            0 if item["severity"] == HIGH else 1 if item["severity"] == MEDIUM else 2,
            -int(item["count"]),
            item["title"],
        )
    )
    return items


def build_violation_overview_counts(
    *,
    events: List[Dict[str, Any]],
    features: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    features = features or {}
    clipboard_count = int(safe_float(features.get("paste_count"), _count_event_type(events, "clipboard")))
    tab_switch_count = int(safe_float(features.get("tab_hidden_count"), sum(1 for event in events if _visibility_hidden(event))))
    focus_blur_count = sum(1 for event in events if _focus_blur(event))
    idle_spike_count = int(safe_float(features.get("idle_spike_count"), _count_event_type(events, "idle_state")))
    idle_max_seconds = max(
        [safe_float(features.get("idle_max_duration_s"), 0.0), *[_idle_event_seconds(event) for event in events if event.get("event_type") == "idle_state"]],
        default=0.0,
    )
    rapid_answer_burst_count = int(_rapid_answer_burst_metrics(events)["count"])
    suspicious_sequence_count = int(_suspicious_sequence_metrics(events)["count"])
    keystroke_anomaly_count = sum(
        1
        for event in events
        if "keystroke" in str(event.get("event_type") or "").lower()
        or find_nested_value(event.get("payload"), ["keystroke_anomaly", "typing_anomaly", "keyboard_anomaly"]) in (True, 1, "1")
    )
    focus_loss_rate = round((focus_blur_count / len(events)) * 100) if events else 0

    return {
        "clipboard_count": clipboard_count,
        "tab_switch_count": tab_switch_count,
        "focus_blur_count": focus_blur_count,
        "idle_spike_count": idle_spike_count,
        "idle_max_duration_s": int(idle_max_seconds),
        "rapid_answer_burst_count": rapid_answer_burst_count,
        "suspicious_sequence_count": suspicious_sequence_count,
        "keystroke_anomaly_count": keystroke_anomaly_count,
        "focus_loss_rate": focus_loss_rate,
    }
