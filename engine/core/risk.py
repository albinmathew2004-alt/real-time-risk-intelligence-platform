from __future__ import annotations

from typing import Dict

from .types import ConfidenceConfig, Features, RiskConfig, RiskLevel, Signals


def _clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else (1.0 if x >= 1.0 else x)


def assign_risk(score: float, combined: Dict[str, float], cfg: RiskConfig) -> RiskLevel:
    elevated_count = int(combined.get("elevated_count", 0.0) or 0)
    support_count = int(combined.get("support_count", 0.0) or 0)
    strong_count = int(combined.get("strong_count", 0.0) or 0)

    # =========================
    # 🔥 HIGH (make stricter)
    # =========================
    if score >= 0.80 and (
        elevated_count >= 2
        or strong_count >= 1
        or support_count >= 4
    ):
        return "HIGH"

    # =========================
    # 🔥 MEDIUM (make easier)
    # =========================
    if score >= 0.45 and (
        support_count >= 1
        or elevated_count >= 1
    ):
        return "MEDIUM"

    return "LOW"


def compute_confidence(features: Features, signals: Signals, combined: Dict[str, float], cfg: ConfidenceConfig) -> float:
    """Confidence reflects evidence strength + data sufficiency."""

    questions = int(features.get("questions_seen", 0) or 0)
    duration_s = float(features.get("attempt_duration_s", 0.0) or 0.0)

    # =========================
    # DATA CONFIDENCE
    # =========================
    q_term = min(1.0, questions / max(1, cfg.min_questions_for_full_conf))
    d_term = min(1.0, duration_s / max(1.0, cfg.min_duration_s_for_full_conf))
    data_conf = 0.55 * q_term + 0.45 * d_term

    # =========================
    # EVIDENCE CONFIDENCE
    # =========================
    elevated_count = int(combined.get("elevated_count", 0.0) or 0)
    strong_count = int(combined.get("strong_count", 0.0) or 0)

    evidence_conf = min(1.0, 0.30 * elevated_count + 0.20 * strong_count)

    conf = 0.60 * data_conf + 0.40 * evidence_conf

    # =========================
    # MARGIN (less aggressive)
    # =========================
    score = float(combined.get("combined_score", 0.0) or 0.0)

    d_med = abs(score - 0.45)
    d_high = abs(score - 0.80)

    margin = min(d_med, d_high)
    margin_term = min(1.0, margin / 0.30)

    conf = 0.75 * conf + 0.25 * margin_term

    # =========================
    # PENALTIES
    # =========================
    seq_gaps = int(features.get("client_seq_gaps", 0) or 0)
    conf -= cfg.seq_gap_penalty_per_gap * seq_gaps

    if not bool(features.get("has_submit_event", False)):
        conf -= cfg.missing_submit_penalty

    return float(_clamp01(conf))