from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


Event = Dict[str, Any]


@dataclass(frozen=True)
class SessionIntelligenceResult:
    adjusted_session_score: float
    session_confidence: float
    risk_momentum: float
    risk_decay_applied: float
    correlation_amplification: float
    session_narrative: str
    timeline_points: List[Dict[str, Any]]
    explanation_factors: List[str]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _risk_level(score: float) -> str:
    if score >= 0.80:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    return "LOW"


def _event_time(event: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_ts(event.get("occurred_at"))


def _first_event_time(events: Sequence[Event]) -> Optional[str]:
    for event in events:
        if event.get("occurred_at"):
            return str(event.get("occurred_at"))
    return None


def _last_event_time(events: Sequence[Event]) -> Optional[str]:
    for event in reversed(list(events)):
        if event.get("occurred_at"):
            return str(event.get("occurred_at"))
    return None


def _pattern_family(pattern_type: str) -> str:
    text = str(pattern_type or "").upper()
    if "PASTE" in text or "CLIPBOARD" in text:
        return "clipboard"
    if "FOCUS" in text or "TAB" in text:
        return "focus"
    if "IDLE" in text:
        return "idle"
    if "TYPING" in text:
        return "typing"
    if "ANSWER" in text or "SUBMISSION" in text:
        return "answer"
    return "other"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def _score_suspicious_event(event: Event, features: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    event_type = str(event.get("event_type") or "").lower()
    payload = event.get("payload") or {}
    timestamp = event.get("occurred_at")
    if not timestamp:
        return None

    if event_type == "clipboard" and str(payload.get("action") or payload.get("operation") or "").lower() == "paste":
        return {"timestamp": str(timestamp), "impact": 0.08, "trigger": "Clipboard activity"}

    if event_type == "visibility_change":
        state = str(payload.get("state") or payload.get("visibility_state") or "").lower()
        if "hidden" in state or "blur" in state:
            return {"timestamp": str(timestamp), "impact": 0.05, "trigger": "Focus loss"}

    if event_type == "idle_state" and str(payload.get("state") or "").lower() == "active":
        duration = _safe_float(payload.get("duration_seconds") or payload.get("duration_s") or features.get("idle_max_duration_s"), 0.0)
        if duration >= 20:
            return {"timestamp": str(timestamp), "impact": 0.04, "trigger": "Idle recovery"}

    if event_type == "typing_burst":
        burst_length = _safe_float(payload.get("burst_length") or payload.get("keystroke_count") or payload.get("count"), 0.0)
        interval_ms = _safe_float(payload.get("interval_ms") or payload.get("avg_interval_ms"), 0.0)
        if burst_length >= 10 or (burst_length >= 8 and 0 < interval_ms <= 90):
            return {"timestamp": str(timestamp), "impact": 0.04, "trigger": "Typing burst"}

    if event_type in {"question_answer", "exam_submitted"}:
        return {"timestamp": str(timestamp), "impact": 0.03, "trigger": "Answer activity"}

    return None


def _sorted_milestones(
    *,
    events: Sequence[Event],
    features: Mapping[str, Any],
    patterns: Sequence[Any],
) -> List[Dict[str, Any]]:
    milestones: List[Dict[str, Any]] = []

    for event in events:
        milestone = _score_suspicious_event(event, features)
        if milestone:
            milestones.append(milestone)

    for pattern in patterns:
        evidence = getattr(pattern, "evidence", {}) or {}
        timestamp = evidence.get("last_seen") or evidence.get("last") or evidence.get("answer_time") or evidence.get("question_leave_at") or evidence.get("fast_leave_at") or evidence.get("paste_at") or evidence.get("first_seen")
        if not timestamp:
            continue
        count = int(evidence.get("count", 1) or 1)
        family = _pattern_family(getattr(pattern, "pattern_type", ""))
        impact = min(0.14, 0.05 + 0.025 * count)
        milestones.append(
            {
                "timestamp": str(timestamp),
                "impact": impact,
                "trigger": evidence.get("reviewer_summary") or str(getattr(pattern, "pattern_type", family)).replace("_", " ").title(),
                "family": family,
            }
        )

    milestones.sort(key=lambda item: _parse_ts(item["timestamp"]) or datetime.min)
    return milestones


def _max_density_bonus(milestones: Sequence[Dict[str, Any]], seconds: int, weight: float) -> float:
    best = 0.0
    for index, milestone in enumerate(milestones):
        anchor = _parse_ts(milestone["timestamp"])
        if anchor is None:
            continue
        window_sum = 0.0
        for candidate in milestones[index:]:
            candidate_time = _parse_ts(candidate["timestamp"])
            if candidate_time is None:
                continue
            delta = (candidate_time - anchor).total_seconds()
            if delta > seconds:
                break
            window_sum += float(candidate.get("impact", 0.0))
        best = max(best, window_sum * weight)
    return min(best, 0.22 if seconds <= 30 else 0.16)


def _normal_tail_decay(events: Sequence[Event], milestones: Sequence[Dict[str, Any]], base_risk: str) -> tuple[float, List[str]]:
    factors: List[str] = []
    if base_risk == "HIGH" or not events or not milestones:
        return 0.0, factors

    last_event = _parse_ts(_last_event_time(events))
    last_suspicious = _parse_ts(milestones[-1]["timestamp"])
    if last_event is None or last_suspicious is None:
        return 0.0, factors

    quiet_seconds = max(0.0, (last_event - last_suspicious).total_seconds())
    if quiet_seconds < 60:
        return 0.0, factors

    decay = min(0.14, 0.04 + ((quiet_seconds - 60.0) / 120.0) * 0.08)
    factors.append(
        f"Normal behavior after the last suspicious sequence reduced session pressure by {decay:.2f}."
    )
    return max(0.0, decay), factors


def _signal_family_count(signals: Mapping[str, Any]) -> int:
    count = 0
    for signal in (signals or {}).values():
        score = _safe_float((signal or {}).get("score"), 0.0)
        if score >= 0.2:
            count += 1
    return count


def _confidence_from_session(
    *,
    features: Mapping[str, Any],
    signals: Mapping[str, Any],
    patterns: Sequence[Any],
    milestones: Sequence[Dict[str, Any]],
) -> float:
    questions = int(features.get("questions_seen", 0) or 0)
    duration_s = _safe_float(features.get("attempt_duration_s"), 0.0)
    signal_families = _signal_family_count(signals)
    repeated_patterns = sum(int((getattr(pattern, "evidence", {}) or {}).get("count", 1) or 1) for pattern in patterns)
    pattern_count = len(patterns)
    milestone_count = len(milestones)

    data_term = min(1.0, questions / 12.0) * 0.45 + min(1.0, duration_s / 900.0) * 0.25
    consistency_term = min(1.0, signal_families / 4.0) * 0.15 + min(1.0, pattern_count / 3.0) * 0.10
    reinforcement_term = min(1.0, repeated_patterns / 5.0) * 0.10 + min(1.0, milestone_count / 8.0) * 0.05
    return _clamp01(data_term + consistency_term + reinforcement_term)


def _build_timeline_points(
    *,
    events: Sequence[Event],
    base_score: float,
    adjusted_score: float,
    milestones: Sequence[Dict[str, Any]],
    decay_applied: float,
    session_confidence: float,
) -> List[Dict[str, Any]]:
    if not events:
        return []

    points: List[Dict[str, Any]] = []
    start_time = _first_event_time(events)
    if start_time:
        baseline = min(base_score, max(0.02, base_score * 0.35))
        points.append(
            {
                "timestamp": start_time,
                "score": round(baseline, 4),
                "risk_level": _risk_level(baseline),
                "trigger": "Session baseline established",
                "confidence": round(session_confidence, 4),
            }
        )

    running_score = points[0]["score"] if points else min(base_score, 0.05)
    seen_triggers = set()
    for milestone in milestones:
        trigger = str(milestone.get("trigger") or "Behavioral signal")
        key = (milestone.get("timestamp"), trigger)
        if key in seen_triggers:
            continue
        seen_triggers.add(key)
        running_score = min(adjusted_score, running_score + float(milestone.get("impact", 0.0)))
        points.append(
            {
                "timestamp": milestone["timestamp"],
                "score": round(running_score, 4),
                "risk_level": _risk_level(running_score),
                "trigger": trigger,
                "confidence": round(session_confidence, 4),
            }
        )

    if decay_applied > 0 and points:
        last_time = _last_event_time(events)
        decay_score = max(points[-1]["score"] - decay_applied, min(base_score, adjusted_score))
        points.append(
            {
                "timestamp": last_time,
                "score": round(decay_score, 4),
                "risk_level": _risk_level(decay_score),
                "trigger": "Risk cooled after sustained normal behavior",
                "confidence": round(session_confidence, 4),
            }
        )

    if points:
        points[-1] = {
            **points[-1],
            "score": round(adjusted_score, 4),
            "risk_level": _risk_level(adjusted_score),
            "confidence": round(session_confidence, 4),
        }

    compact: List[Dict[str, Any]] = []
    for point in points:
        if compact and compact[-1]["timestamp"] == point["timestamp"] and compact[-1]["risk_level"] == point["risk_level"]:
            compact[-1] = point
        else:
            compact.append(point)
    return compact


def build_session_intelligence(
    *,
    events: Sequence[Event],
    features: Mapping[str, Any],
    signals: Mapping[str, Any],
    patterns: Sequence[Any],
    current_base_score: float,
    current_base_risk: str,
) -> SessionIntelligenceResult:
    milestones = _sorted_milestones(events=events, features=features, patterns=patterns)

    momentum_30 = _max_density_bonus(milestones, 30, 1.0)
    momentum_120 = _max_density_bonus(milestones, 120, 0.65)
    risk_momentum = _clamp01(momentum_30 + momentum_120)

    repeated_pattern_events = sum(max(0, int((getattr(pattern, "evidence", {}) or {}).get("count", 1) or 1) - 1) for pattern in patterns)
    correlation_amplification = min(0.18, repeated_pattern_events * 0.025 + len(patterns) * 0.01)

    decay_applied, decay_factors = _normal_tail_decay(events, milestones, current_base_risk)

    adjusted_score = current_base_score + risk_momentum * 0.18 + correlation_amplification - decay_applied
    if current_base_risk != "HIGH":
        adjusted_score = min(0.79, adjusted_score)
    else:
        adjusted_score = max(current_base_score, adjusted_score)
        decay_applied = 0.0

    adjusted_score = _clamp01(adjusted_score)
    session_confidence = _confidence_from_session(
        features=features,
        signals=signals,
        patterns=patterns,
        milestones=milestones,
    )

    explanation_factors: List[str] = []
    if correlation_amplification > 0:
        explanation_factors.append("Repeated correlated suspicious sequences increased risk confidence.")
    if risk_momentum > 0.1:
        explanation_factors.append("Suspicious behavior was clustered inside short time windows.")
    explanation_factors.extend(decay_factors)
    if int(features.get("typing_behavior_anomaly_count", 0) or 0) > 0:
        explanation_factors.append("Typing behavior showed metadata-only irregularities after idle or paste activity.")
    if not explanation_factors:
        explanation_factors.append("Behavior remained mostly stable across the session.")

    if current_base_risk == "HIGH":
        session_narrative = "Strong deterministic evidence kept the attempt in the HIGH risk band throughout the session."
    elif patterns:
        top_pattern = getattr(patterns[0], "evidence", {}) or {}
        session_narrative = (
            top_pattern.get("reviewer_summary")
            or "Repeated suspicious behavioral chains kept the attempt elevated despite later normal activity."
        )
    elif risk_momentum > 0.08:
        session_narrative = "Suspicious activity was clustered in short windows rather than spread randomly."
    elif decay_applied > 0.03:
        session_narrative = "Risk stabilized after a period of normal behavior, but earlier suspicious activity remained relevant."
    else:
        session_narrative = "Behavioral risk evolved gradually based on the timing and consistency of observed signals."

    timeline_points = _build_timeline_points(
        events=events,
        base_score=current_base_score,
        adjusted_score=adjusted_score,
        milestones=milestones,
        decay_applied=decay_applied,
        session_confidence=session_confidence,
    )

    return SessionIntelligenceResult(
        adjusted_session_score=round(adjusted_score, 4),
        session_confidence=round(session_confidence, 4),
        risk_momentum=round(risk_momentum, 4),
        risk_decay_applied=round(decay_applied, 4),
        correlation_amplification=round(correlation_amplification, 4),
        session_narrative=session_narrative,
        timeline_points=timeline_points,
        explanation_factors=explanation_factors,
    )
