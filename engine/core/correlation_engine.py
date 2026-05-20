from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence


Event = Dict[str, Any]


@dataclass(frozen=True)
class CorrelationMatch:
    sequence_type: str
    severity: str
    count: int
    confidence: float
    involved_event_types: List[str]
    first_seen: Optional[str]
    last_seen: Optional[str]
    explanation: str
    reviewer_summary: str
    related_event_count: int


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


def _event_time(event: Event) -> Optional[datetime]:
    return _parse_ts(event.get("occurred_at"))


def _sort_key(event: Event) -> float:
    timestamp = _event_time(event)
    return timestamp.timestamp() if timestamp is not None else 0.0


def _payload_value(event: Event, key: str, default: Any = None) -> Any:
    payload = event.get("payload") or {}
    return payload.get(key, default)


def _focus_loss(event: Event) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    if event_type in {"window_blur", "blur"}:
        return True
    if event_type != "visibility_change":
        return False
    state = str((_payload_value(event, "state") or _payload_value(event, "visibility_state") or "")).lower()
    return "hidden" in state or "blur" in state


def _focus_return(event: Event) -> bool:
    if str(event.get("event_type") or "").lower() != "visibility_change":
        return False
    state = str((_payload_value(event, "state") or _payload_value(event, "visibility_state") or "")).lower()
    return "visible" in state or "focus" in state


def _clipboard_paste(event: Event) -> bool:
    if str(event.get("event_type") or "").lower() != "clipboard":
        return False
    action = str((_payload_value(event, "action") or _payload_value(event, "operation") or "")).lower()
    return action == "paste"


def _answer_change(event: Event) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    if event_type in {"question_answer", "exam_submitted"}:
        return True
    return event_type == "question_view" and str(_payload_value(event, "action") or "").lower() == "leave"


def _idle_recovery(event: Event) -> bool:
    if str(event.get("event_type") or "").lower() != "idle_state":
        return False
    return str(_payload_value(event, "state") or "").lower() == "active"


def _idle_duration_s(event: Event) -> float:
    payload = event.get("payload") or {}
    for key in ("duration_s", "idle_duration_s", "duration_seconds", "seconds", "gap_s"):
        try:
            value = float(payload.get(key, 0.0) or 0.0)
        except Exception:
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _typing_burst(event: Event) -> bool:
    return str(event.get("event_type") or "").lower() == "typing_burst"


def _backspace_activity(event: Event) -> bool:
    return str(event.get("event_type") or "").lower() == "backspace_activity"


def _typing_recovery_event(event: Event) -> bool:
    return _typing_burst(event) or _backspace_activity(event)


def _typing_recovery_strength(event: Event) -> int:
    if _backspace_activity(event):
        try:
            return int((_payload_value(event, "count") or _payload_value(event, "backspace_count") or 0))
        except Exception:
            return 0
    if _typing_burst(event):
        try:
            return int((_payload_value(event, "burst_length") or _payload_value(event, "keystroke_count") or _payload_value(event, "count") or 0))
        except Exception:
            return 0
    return 0


def _rapid_answer_delta(events: Sequence[Event], index: int) -> Optional[float]:
    if not _answer_change(events[index]):
        return None
    current_time = _event_time(events[index])
    previous_answer_time = None
    for previous_index in range(index - 1, -1, -1):
        if not _answer_change(events[previous_index]):
            continue
        previous_answer_time = _event_time(events[previous_index])
        break
    if current_time is None or previous_answer_time is None:
        return None
    return max(0.0, (current_time - previous_answer_time).total_seconds())


def _confidence(base: float, count: int, closeness_bonus: float = 0.0) -> float:
    return max(0.0, min(0.95, base + min(0.2, count * 0.06) + closeness_bonus))


def _make_match(
    *,
    sequence_type: str,
    severity: str,
    count: int,
    confidence: float,
    involved_event_types: List[str],
    first_seen: Optional[str],
    last_seen: Optional[str],
    explanation: str,
    reviewer_summary: str,
    related_event_count: int,
) -> CorrelationMatch:
    return CorrelationMatch(
        sequence_type=sequence_type,
        severity=severity,
        count=count,
        confidence=round(confidence, 3),
        involved_event_types=involved_event_types,
        first_seen=first_seen,
        last_seen=last_seen,
        explanation=explanation,
        reviewer_summary=reviewer_summary,
        related_event_count=related_event_count,
    )


def detect_correlated_sequences(events: List[Event]) -> List[CorrelationMatch]:
    ordered = sorted(events, key=_sort_key)
    if not ordered:
        return []

    matches: List[CorrelationMatch] = []

    # 1. CLIPBOARD_AFTER_FOCUS_LOSS within 15 seconds
    clipboard_after_focus_occurrences: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        if not _clipboard_paste(event):
            continue
        paste_time = _event_time(event)
        if paste_time is None:
            continue
        for previous_index in range(index - 1, -1, -1):
            previous = ordered[previous_index]
            previous_time = _event_time(previous)
            if previous_time is None:
                continue
            delta = (paste_time - previous_time).total_seconds()
            if delta > 15:
                break
            if _focus_loss(previous):
                clipboard_after_focus_occurrences.append({
                    "delta": delta,
                    "focus_time": previous.get("occurred_at"),
                    "paste_time": event.get("occurred_at"),
                })
                break
    if clipboard_after_focus_occurrences:
        first_seen = clipboard_after_focus_occurrences[0]["focus_time"]
        last_seen = clipboard_after_focus_occurrences[-1]["paste_time"]
        fastest = min(item["delta"] for item in clipboard_after_focus_occurrences)
        count = len(clipboard_after_focus_occurrences)
        matches.append(
            _make_match(
                sequence_type="CLIPBOARD_AFTER_FOCUS_LOSS",
                severity="MEDIUM" if count >= 2 else "LOW",
                count=count,
                confidence=_confidence(0.58, count, 0.08 if fastest <= 8 else 0.0),
                involved_event_types=["visibility_change", "window_blur", "clipboard"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Clipboard activity occurred within {int(round(fastest))} seconds after the candidate left the assessment window. "
                    f"This sequence repeated {count} time{'s' if count != 1 else ''}."
                ),
                reviewer_summary="Focus loss was followed quickly by clipboard activity.",
                related_event_count=count * 2,
            )
        )

    # 2. RAPID_SUBMISSION_AFTER_PASTE within 20 seconds
    rapid_submission_after_paste: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        if not _clipboard_paste(event):
            continue
        paste_time = _event_time(event)
        if paste_time is None:
            continue
        for next_index in range(index + 1, len(ordered)):
            nxt = ordered[next_index]
            nxt_time = _event_time(nxt)
            if nxt_time is None:
                continue
            delta = (nxt_time - paste_time).total_seconds()
            if delta > 20:
                break
            if _answer_change(nxt):
                rapid_submission_after_paste.append({
                    "delta": delta,
                    "paste_time": event.get("occurred_at"),
                    "answer_time": nxt.get("occurred_at"),
                })
                break
    if rapid_submission_after_paste:
        first_seen = rapid_submission_after_paste[0]["paste_time"]
        last_seen = rapid_submission_after_paste[-1]["answer_time"]
        fastest = min(item["delta"] for item in rapid_submission_after_paste)
        count = len(rapid_submission_after_paste)
        matches.append(
            _make_match(
                sequence_type="RAPID_SUBMISSION_AFTER_PASTE",
                severity="MEDIUM",
                count=count,
                confidence=_confidence(0.62, count, 0.1 if fastest <= 10 else 0.0),
                involved_event_types=["clipboard", "question_answer", "exam_submitted", "question_view"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Answer activity followed a paste event within {int(round(fastest))} seconds. "
                    f"This sequence occurred {count} time{'s' if count != 1 else ''}."
                ),
                reviewer_summary="Paste activity was followed quickly by answer submission or question exit.",
                related_event_count=count * 2,
            )
        )

    # 3. IDLE_TO_BURST within 30 seconds
    idle_to_burst: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        if not _idle_recovery(event):
            continue
        idle_time = _event_time(event)
        idle_duration = _idle_duration_s(event)
        if idle_time is None or idle_duration < 30:
            continue
        for next_index in range(index + 1, len(ordered)):
            nxt = ordered[next_index]
            nxt_time = _event_time(nxt)
            if nxt_time is None:
                continue
            delta = (nxt_time - idle_time).total_seconds()
            if delta > 30:
                break
            if _typing_burst(nxt) or (_answer_change(nxt) and ((_rapid_answer_delta(ordered, next_index) or 999) <= 15)):
                idle_to_burst.append({
                    "delta": delta,
                    "idle_time": event.get("occurred_at"),
                    "burst_time": nxt.get("occurred_at"),
                })
                break
    if idle_to_burst:
        first_seen = idle_to_burst[0]["idle_time"]
        last_seen = idle_to_burst[-1]["burst_time"]
        closest = min(item["delta"] for item in idle_to_burst)
        count = len(idle_to_burst)
        matches.append(
            _make_match(
                sequence_type="IDLE_TO_BURST",
                severity="MEDIUM" if count >= 2 else "LOW",
                count=count,
                confidence=_confidence(0.54, count, 0.08 if closest <= 12 else 0.0),
                involved_event_types=["idle_state", "typing_burst", "question_answer"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Long idle periods were followed by immediate typing or answer bursts within {int(round(closest))} seconds. "
                    f"This occurred {count} time{'s' if count != 1 else ''}."
                ),
                reviewer_summary="Idle recovery was followed quickly by burst-like answering behavior.",
                related_event_count=count * 2,
            )
        )

    # 4. TAB_SWITCH_TO_ANSWER_CHANGE within 15 seconds of returning
    tab_switch_to_answer: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        if not _focus_loss(event):
            continue
        hidden_time = _event_time(event)
        if hidden_time is None:
            continue
        return_event = None
        return_time = None
        for next_index in range(index + 1, len(ordered)):
            nxt = ordered[next_index]
            nxt_time = _event_time(nxt)
            if nxt_time is None:
                continue
            delta_hidden = (nxt_time - hidden_time).total_seconds()
            if delta_hidden > 60:
                break
            if _focus_return(nxt):
                return_event = nxt
                return_time = nxt_time
                break
        if return_event is None or return_time is None:
            continue
        start_index = ordered.index(return_event)
        for follow_index in range(start_index + 1, len(ordered)):
            follow = ordered[follow_index]
            follow_time = _event_time(follow)
            if follow_time is None:
                continue
            delta = (follow_time - return_time).total_seconds()
            if delta > 15:
                break
            if _answer_change(follow):
                tab_switch_to_answer.append({
                    "delta": delta,
                    "hidden_time": event.get("occurred_at"),
                    "return_time": return_event.get("occurred_at"),
                    "answer_time": follow.get("occurred_at"),
                })
                break
    if tab_switch_to_answer:
        first_seen = tab_switch_to_answer[0]["hidden_time"]
        last_seen = tab_switch_to_answer[-1]["answer_time"]
        closest = min(item["delta"] for item in tab_switch_to_answer)
        count = len(tab_switch_to_answer)
        matches.append(
            _make_match(
                sequence_type="TAB_SWITCH_TO_ANSWER_CHANGE",
                severity="MEDIUM" if count >= 2 else "LOW",
                count=count,
                confidence=_confidence(0.56, count, 0.09 if closest <= 6 else 0.0),
                involved_event_types=["visibility_change", "question_answer"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Candidate returned from focus loss and changed an answer within {int(round(closest))} seconds. "
                    f"This occurred {count} time{'s' if count != 1 else ''}."
                ),
                reviewer_summary="Answer changes closely followed tab or focus returns.",
                related_event_count=count * 3,
            )
        )

    # 5. REPEATED_FOCUS_LOSS_PATTERN: 3+ focus-loss events within 2 minutes
    focus_events = [event for event in ordered if _focus_loss(event)]
    focus_clusters: List[Dict[str, Any]] = []
    cluster: List[Event] = []
    for event in focus_events:
        if not cluster:
            cluster = [event]
            continue
        current_time = _event_time(event)
        cluster_start = _event_time(cluster[0])
        if current_time is None or cluster_start is None:
            continue
        if (current_time - cluster_start).total_seconds() <= 120:
            cluster.append(event)
        else:
            if len(cluster) >= 3:
                focus_clusters.append({
                    "count": len(cluster),
                    "first": cluster[0].get("occurred_at"),
                    "last": cluster[-1].get("occurred_at"),
                })
            cluster = [event]
    if len(cluster) >= 3:
        focus_clusters.append({
            "count": len(cluster),
            "first": cluster[0].get("occurred_at"),
            "last": cluster[-1].get("occurred_at"),
        })
    if focus_clusters:
        cluster_count = len(focus_clusters)
        event_total = sum(item["count"] for item in focus_clusters)
        first_seen = focus_clusters[0]["first"]
        last_seen = focus_clusters[-1]["last"]
        matches.append(
            _make_match(
                sequence_type="REPEATED_FOCUS_LOSS_PATTERN",
                severity="MEDIUM",
                count=cluster_count,
                confidence=_confidence(0.6, cluster_count, 0.06 if event_total >= 5 else 0.0),
                involved_event_types=["visibility_change", "window_blur", "blur"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Repeated focus loss clusters were detected {cluster_count} time{'s' if cluster_count != 1 else ''}, "
                    f"covering {event_total} focus-loss events within short two-minute windows."
                ),
                reviewer_summary="Multiple focus-loss events were clustered tightly together.",
                related_event_count=event_total,
            )
        )

    # 6. PASTE_WITHOUT_TYPING within 20 seconds
    paste_without_typing: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        if not _clipboard_paste(event):
            continue
        paste_time = _event_time(event)
        if paste_time is None:
            continue
        seen_typing_recovery = False
        for next_index in range(index + 1, len(ordered)):
            nxt = ordered[next_index]
            nxt_time = _event_time(nxt)
            if nxt_time is None:
                continue
            delta = (nxt_time - paste_time).total_seconds()
            if delta > 20:
                break
            if _typing_recovery_event(nxt) and _typing_recovery_strength(nxt) >= 3:
                seen_typing_recovery = True
            if _answer_change(nxt):
                if not seen_typing_recovery:
                    paste_without_typing.append({
                        "delta": delta,
                        "paste_time": event.get("occurred_at"),
                        "answer_time": nxt.get("occurred_at"),
                    })
                break
    if paste_without_typing:
        count = len(paste_without_typing)
        first_seen = paste_without_typing[0]["paste_time"]
        last_seen = paste_without_typing[-1]["answer_time"]
        closest = min(item["delta"] for item in paste_without_typing)
        matches.append(
            _make_match(
                sequence_type="PASTE_WITHOUT_TYPING",
                severity="MEDIUM",
                count=count,
                confidence=_confidence(0.64, count, 0.1 if closest <= 10 else 0.0),
                involved_event_types=["clipboard", "typing_burst", "backspace_activity", "question_answer"],
                first_seen=first_seen,
                last_seen=last_seen,
                explanation=(
                    f"Paste activity was followed by answer submission with minimal typing recovery within {int(round(closest))} seconds. "
                    f"This sequence occurred {count} time{'s' if count != 1 else ''}."
                ),
                reviewer_summary="Pasted material was submitted with little follow-up typing behavior.",
                related_event_count=count * 2,
            )
        )

    return matches
