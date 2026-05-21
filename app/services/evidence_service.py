from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from engine.core.correlation_engine import detect_correlated_sequences


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
TYPING_BEHAVIOR = "TYPING_BEHAVIOR"
CORRELATED_PATTERN = "CORRELATED_PATTERN"
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


def _correlation_items(events: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    patterns = detect_correlated_sequences(events)
    items: List[Dict[str, Any]] = []
    total_count = 0
    total_related_events = 0
    first_seen = None
    last_seen = None

    for pattern in patterns:
        total_count += int(pattern.count)
        total_related_events += int(pattern.related_event_count)
        if first_seen is None and pattern.first_seen:
            first_seen = pattern.first_seen
        if pattern.last_seen:
            last_seen = pattern.last_seen

        items.append(
            {
                "signal_type": pattern.sequence_type,
                "title": pattern.sequence_type.replace("_", " ").title(),
                "severity": pattern.severity,
                "count": int(pattern.count),
                "explanation": pattern.explanation,
                "first_seen": pattern.first_seen,
                "last_seen": pattern.last_seen,
                "related_event_count": int(pattern.related_event_count),
                "correlation_confidence": pattern.confidence,
                "reviewer_summary": pattern.reviewer_summary,
                "involved_event_types": list(pattern.involved_event_types),
                "evidence_category": CORRELATED_PATTERN,
            }
        )

    aggregate = {
        "count": total_count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "related_event_count": total_related_events,
        "pattern_count": len(patterns),
    }
    return items, aggregate


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


def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _typing_event(event: Dict[str, Any]) -> bool:
    return str(event.get("event_type") or "").lower() in {
        "typing_started",
        "typing_stopped",
        "typing_pause",
        "typing_burst",
        "backspace_activity",
    }


def _typing_pause_duration(event: Dict[str, Any]) -> float:
    payload = event.get("payload") or {}
    seconds = safe_float(find_nested_value(payload, ["duration_s", "pause_duration_s", "duration_seconds"]), 0.0)
    if seconds > 0:
        return seconds
    return safe_float(find_nested_value(payload, ["pause_duration_ms", "duration_ms"]), 0.0) / 1000.0


def _typing_burst_metrics(events: List[Dict[str, Any]], features: Dict[str, Any]) -> Dict[str, Any]:
    typing_events = [event for event in events if _typing_event(event)]
    typing_times = [str(event.get("occurred_at")) for event in typing_events if event.get("occurred_at")]
    typing_bursts = [event for event in events if str(event.get("event_type") or "").lower() == "typing_burst"]
    backspace_events = [event for event in events if str(event.get("event_type") or "").lower() == "backspace_activity"]

    rapid_typing_sequences = int(safe_float(features.get("rapid_typing_sequences"), 0.0))
    if rapid_typing_sequences <= 0:
        for event in typing_bursts:
            payload = event.get("payload") or {}
            burst_length = safe_float(find_nested_value(payload, ["burst_length", "keystroke_count", "count"]), 0.0)
            interval_ms = safe_float(find_nested_value(payload, ["interval_ms", "avg_interval_ms", "mean_interval_ms"]), 0.0)
            duration_ms = safe_float(find_nested_value(payload, ["duration_ms", "burst_duration_ms"]), 0.0)
            if burst_length >= 12 or (burst_length >= 8 and 0 < interval_ms <= 90) or (burst_length >= 8 and 0 < duration_ms <= 1500):
                rapid_typing_sequences += 1

    avg_pause_duration = safe_float(features.get("avg_pause_duration"), 0.0)
    if avg_pause_duration <= 0:
        pauses = [_typing_pause_duration(event) for event in events if str(event.get("event_type") or "").lower() == "typing_pause"]
        pauses = [value for value in pauses if value > 0]
        avg_pause_duration = sum(pauses) / len(pauses) if pauses else 0.0

    paste_without_typing = int(safe_float(features.get("paste_without_typing"), 0.0))
    abnormal_pause_recovery = int(safe_float(features.get("abnormal_pause_recovery"), 0.0))
    consistency = safe_float(features.get("typing_consistency_score"), 1.0 if typing_events else 0.0)
    burst_count = int(safe_float(features.get("typing_burst_count"), len(typing_bursts)))
    backspace_count = int(safe_float(features.get("backspace_activity_count"), sum(int(safe_float((event.get("payload") or {}).get("count"), 1.0)) for event in backspace_events)))
    anomaly_count = int(
        safe_float(
            features.get("typing_behavior_anomaly_count"),
            rapid_typing_sequences + paste_without_typing + abnormal_pause_recovery + (1 if typing_events and consistency < 0.55 else 0),
        )
    )
    first_seen, last_seen = _first_last(typing_times)

    return {
        "typing_events": typing_events,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "typing_burst_count": burst_count,
        "avg_pause_duration": avg_pause_duration,
        "rapid_typing_sequences": rapid_typing_sequences,
        "paste_without_typing": paste_without_typing,
        "abnormal_pause_recovery": abnormal_pause_recovery,
        "typing_consistency_score": consistency,
        "typing_behavior_anomaly_count": anomaly_count,
        "backspace_activity_count": backspace_count,
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
    correlation_items, correlation_aggregate = _correlation_items(events)

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
                explanation=(
                    f"Clipboard metadata was recorded {clipboard_count} {_pluralize(clipboard_count, 'time')} during the attempt, "
                    "which should be reviewed alongside nearby focus and answer activity."
                ),
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
                explanation=(
                    f"The assessment window lost visibility {tab_switch_count} {_pluralize(tab_switch_count, 'time')}, "
                    "indicating repeated movement away from the active assessment tab."
                ),
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
                explanation=(
                    f"Window focus changed or blurred {focus_blur_count} {_pluralize(focus_blur_count, 'time')} during the attempt, "
                    "creating repeated gaps in on-screen engagement."
                ),
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
                    f"Idle telemetry recorded {max(idle_spike_count, len(idle_events))} "
                    f"{_pluralize(max(idle_spike_count, len(idle_events)), 'spike')}; longest observed gap was {format_duration(idle_max_seconds)}."
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
                explanation=(
                    f"Answer changes or submissions occurred in quick succession {burst_metrics['count']} "
                    f"{_pluralize(int(burst_metrics['count']), 'time')}, reducing normal dwell time between responses."
                ),
                first_seen=burst_metrics["first_seen"],
                last_seen=burst_metrics["last_seen"],
                related_event_count=int(burst_metrics["related_event_count"]),
            )
        )

    sequence_metrics = correlation_aggregate if correlation_aggregate["count"] > 0 else _suspicious_sequence_metrics(events)
    if sequence_metrics["count"] > 0 and not correlation_items:
        severity = _severity_from_count(int(sequence_metrics["count"]), medium_threshold=1, high_threshold=3)
        items.append(
            _build_item(
                signal_type=SUSPICIOUS_SEQUENCE,
                title="Suspicious Sequence",
                severity=severity,
                count=int(sequence_metrics["count"]),
                explanation=(
                    "Clipboard or focus-loss activity was followed by answer changes inside short event windows "
                    f"{sequence_metrics['count']} {_pluralize(int(sequence_metrics['count']), 'time')}, suggesting linked suspicious behavior."
                ),
                first_seen=sequence_metrics["first_seen"],
                last_seen=sequence_metrics["last_seen"],
                related_event_count=int(sequence_metrics["related_event_count"]),
            )
        )

    items.extend(correlation_items)

    typing_metrics = _typing_burst_metrics(events, features)
    if typing_metrics["typing_behavior_anomaly_count"] > 0:
        severity = MEDIUM if (
            typing_metrics["paste_without_typing"] > 0
            or typing_metrics["abnormal_pause_recovery"] > 0
            or typing_metrics["typing_behavior_anomaly_count"] >= 3
        ) else LOW
        explanations: List[str] = []
        if typing_metrics["paste_without_typing"] > 0:
            explanations.append("Large paste activity occurred with minimal typing recovery.")
        if typing_metrics["abnormal_pause_recovery"] > 0:
            explanations.append("Irregular typing bursts were observed after prolonged pauses.")
        if typing_metrics["rapid_typing_sequences"] > 0:
            explanations.append(
                f"Rapid typing bursts were detected {typing_metrics['rapid_typing_sequences']} time"
                f"{'s' if typing_metrics['rapid_typing_sequences'] != 1 else ''}."
            )
        if not explanations:
            explanations.append(
                f"Typing consistency dropped to {typing_metrics['typing_consistency_score']:.2f} based on metadata-only timing patterns."
            )
        items.append(
            _build_item(
                signal_type=TYPING_BEHAVIOR,
                title="Typing Behavior Anomalies",
                severity=severity,
                count=int(typing_metrics["typing_behavior_anomaly_count"]),
                explanation=" ".join(explanations),
                first_seen=typing_metrics["first_seen"],
                last_seen=typing_metrics["last_seen"],
                related_event_count=len(typing_metrics["typing_events"]),
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
                explanation=(
                    f"The combined risk score reached {safe_float(risk_score):.2f}, reflecting sustained high-concern evidence "
                    "rather than an isolated signal."
                ),
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
    _, correlation_aggregate = _correlation_items(events)
    if correlation_aggregate["count"] > 0:
        suspicious_sequence_count = int(correlation_aggregate["count"])
    typing_metrics = _typing_burst_metrics(events, features)
    typing_behavior_anomaly_count = int(typing_metrics["typing_behavior_anomaly_count"])
    focus_loss_rate = round((focus_blur_count / len(events)) * 100) if events else 0

    return {
        "clipboard_count": clipboard_count,
        "tab_switch_count": tab_switch_count,
        "focus_blur_count": focus_blur_count,
        "idle_spike_count": idle_spike_count,
        "idle_max_duration_s": int(idle_max_seconds),
        "rapid_answer_burst_count": rapid_answer_burst_count,
        "suspicious_sequence_count": suspicious_sequence_count,
        "correlated_pattern_count": int(correlation_aggregate["pattern_count"]),
        "typing_behavior_anomaly_count": typing_behavior_anomaly_count,
        "typing_consistency_score": round(safe_float(typing_metrics["typing_consistency_score"]), 4),
        "keystroke_anomaly_count": typing_behavior_anomaly_count,
        "focus_loss_rate": focus_loss_rate,
    }
