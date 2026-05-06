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

    # --- SIGNALS ---
    timing_score = 1.0 if avg_time < 8 else 0.1
    tab_score = min(1.0, tab_hidden / 3)
    clipboard_score = min(1.0, paste_count / 2)

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
            "score": 0.1,
            "components": {}
        },
    }

    scores = {k: float(v["score"]) for k, v in signals.items()}

    return WeightedSignals(signals=signals, scores=scores)