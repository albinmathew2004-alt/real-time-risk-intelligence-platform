from __future__ import annotations

from typing import Dict, List, Tuple, Optional

from .types import Features, RiskLevel, Signals


def _fmt_pct(x: float) -> str:
    try:
        return f"{100.0 * float(x):.0f}%"
    except Exception:
        return "0%"


def _top_signals(signals: Signals, k: int = 3) -> List[Tuple[str, float]]:
    items: List[Tuple[str, float]] = []

    for name, data in (signals or {}).items():
        try:
            score = float(data.get("score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        items.append((name, score))

    items.sort(key=lambda t: t[1], reverse=True)
    return items[:k]


def generate_explanations(
    features: Features,
    signals: Signals,
    risk: RiskLevel,
    patterns: Optional[List] = None,
) -> Dict[str, List[str]]:
    """Aligned explanation generator (NEVER overrides risk)"""

    summary: List[str] = []
    reasons: List[str] = []

    questions = int((features or {}).get("questions_seen", 0) or 0)
    duration_s = float((features or {}).get("attempt_duration_s", 0.0) or 0.0)

    # ✅ ALWAYS respect risk from engine
    if risk == "HIGH":
        summary.append("Strong, repeated integrity signals were detected during the attempt.")
    elif risk == "MEDIUM":
        summary.append("Behavioral irregularities were detected and warrant reviewer attention.")
    else:
        summary.append("Engagement remained largely stable with limited suspicious behavior.")

    summary.append(f"The session covered {questions} question{'s' if questions != 1 else ''} over approximately {duration_s/60.0:.1f} minutes.")

    # ✅ Pattern info (safe)
    if patterns:
        try:
            top_patterns = sorted(patterns, key=lambda p: float(p.strength), reverse=True)[:2]
            for p in top_patterns:
                summary.append(
                    f"Detected pattern: {p.pattern_type.replace('_', ' ').title()} (strength {float(p.strength):.2f})."
                )
        except Exception:
            pass

    # 🚨 LOW DATA NOTE (BUT DO NOT CHANGE RISK)
    if questions <= 2:
        reasons.append("Limited interaction data was available, so confidence should be interpreted cautiously.")

    # ✅ Signal explanations
    for name, score in _top_signals(signals, k=4):
        if score < 0.50:
            continue

        comp = (signals or {}).get(name, {}).get("components", {}) or {}

        try:
            if name == "tab":
                r = float(comp.get("tab_hidden_ratio", 0.0) or 0.0)
                reasons.append(f"The candidate spent {_fmt_pct(r)} of observed time outside the assessment window.")

            elif name == "clipboard":
                paste_count = int(comp.get("paste_count", 0) or 0)
                reasons.append(f"{paste_count} paste action{'s' if paste_count != 1 else ''} were detected during the attempt.")

            elif name == "timing":
                fast_ratio = float(comp.get("fast_ratio", 0.0) or 0.0)

                if fast_ratio > 0:
                    reasons.append(f"Unusually fast responses were observed across {_fmt_pct(fast_ratio)} of answered questions.")
                else:
                    reasons.append("Answer timing appeared inconsistent with a steady question-by-question response pattern.")

            elif name == "idle":
                idle_ratio = float(comp.get("idle_ratio", 0.0) or 0.0)
                reasons.append(f"Idle behavior was elevated, accounting for {_fmt_pct(idle_ratio)} of the observed session.")

            elif name == "typing":
                paste_without_typing = int(comp.get("paste_without_typing", 0) or 0)
                abnormal_pause_recovery = int(comp.get("abnormal_pause_recovery", 0) or 0)
                rapid_typing_sequences = int(comp.get("rapid_typing_sequences", 0) or 0)
                consistency = float(comp.get("typing_consistency_score", 1.0) or 1.0)

                if paste_without_typing > 0:
                    reasons.append("Typing recovery after paste activity appeared unusually limited.")
                elif abnormal_pause_recovery > 0:
                    reasons.append("Irregular typing bursts appeared after prolonged pauses.")
                elif rapid_typing_sequences > 0:
                    reasons.append(f"{rapid_typing_sequences} rapid typing sequence{'s' if rapid_typing_sequences != 1 else ''} were observed.")
                else:
                    reasons.append(f"Typing consistency declined to {consistency:.2f}.")

        except Exception:
            continue

    if not reasons:
        reasons.append("No strong suspicious patterns were detected in the observed telemetry.")

    return {
        "summary": summary,
        "reasons": reasons,
    }
