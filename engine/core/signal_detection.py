from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .types import Signals, Features


@dataclass
class WeightedSignals:
    signals: Signals
    scores: Dict[str, float]


def detect_signals(features: Features, cfg) -> WeightedSignals:

    paste_count = int(features.get("paste_count", 0) or 0)
    tab_hidden = int(features.get("tab_hidden_count", 0) or 0)
    avg_time = float(features.get("time_per_question_mean_s", 100) or 100)
    typing_burst_count = int(features.get("typing_burst_count", 0) or 0)
    rapid_typing_sequences = int(features.get("rapid_typing_sequences", 0) or 0)
    paste_without_typing = int(features.get("paste_without_typing", 0) or 0)
    abnormal_pause_recovery = int(features.get("abnormal_pause_recovery", 0) or 0)
    typing_consistency_score = float(features.get("typing_consistency_score", 1.0) or 1.0)
    avg_pause_duration = float(features.get("avg_pause_duration", 0.0) or 0.0)
    backspace_activity_count = int(features.get("backspace_activity_count", 0) or 0)
    typing_telemetry_event_count = int(features.get("typing_telemetry_event_count", 0) or 0)

    # --- SIGNALS ---
    timing_score = 1.0 if avg_time < 8 else 0.1
    tab_score = min(1.0, tab_hidden / 3)
    clipboard_score = min(1.0, paste_count / 2)
    if typing_telemetry_event_count <= 0:
        typing_score = 0.0
    else:
        typing_score = min(
            0.66,
            (min(0.24, rapid_typing_sequences * 0.14))
            + (min(0.22, paste_without_typing * 0.18))
            + (min(0.16, abnormal_pause_recovery * 0.12))
            + (max(0.0, 0.55 - typing_consistency_score) * 0.70)
            + (min(0.10, backspace_activity_count / 30.0))
            + (0.06 if typing_burst_count >= 6 and avg_pause_duration <= 1.2 else 0.0)
        )

    signals: Signals = {
        "timing": {
            "score": float(timing_score),
            "components": {
                "avg_time": avg_time
            }
        },
        "tab": {
            "score": float(tab_score),
            "components": {
                "tab_hidden_count": tab_hidden
            }
        },
        "clipboard": {
            "score": float(clipboard_score),
            "components": {
                "paste_count": paste_count
            }
        },
        "idle": {
            "score": 0.1,
            "components": {}
        },
        "answer_changes": {
            "score": 0.1,
            "components": {}
        },
        "typing": {
            "score": float(typing_score),
            "components": {
                "typing_burst_count": typing_burst_count,
                "rapid_typing_sequences": rapid_typing_sequences,
                "paste_without_typing": paste_without_typing,
                "abnormal_pause_recovery": abnormal_pause_recovery,
                "typing_consistency_score": typing_consistency_score,
                "avg_pause_duration": avg_pause_duration,
                "backspace_activity_count": backspace_activity_count,
                "typing_telemetry_event_count": typing_telemetry_event_count,
            }
        },
    }

    scores = {k: float(v["score"]) for k, v in signals.items()}

    return WeightedSignals(signals=signals, scores=scores)
