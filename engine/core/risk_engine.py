from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from engine.ml.model import predict_risk

from .logger import log_attempt
from .event_processor import normalize_events
from .explanations import generate_explanations
from .feature_engineering import build_features
from .pattern_engine import PatternConfig, PatternMatch, detect_patterns
from .reasoning import combine_signals
from .risk import assign_risk, compute_confidence
from .signal_detection import WeightedSignals, detect_signals
from .types import Features, RiskLevel, ScoringConfig, Signals


# =========================
# DATA CLASSES
# =========================

@dataclass(frozen=True)
class RiskResult:
    attempt_id: str
    risk: RiskLevel
    confidence: float
    confidence_score: float
    promoted_by_pattern: bool
    base_score: float
    combined_score: float
    signals: Signals
    patterns: List[PatternMatch]
    explanation: Dict[str, List[str]]
    explanation_text: str


@dataclass(frozen=True)
class ConfidenceScoreConfig:
    support_threshold: float = 0.20
    elevated_threshold: float = 0.60
    strong_threshold: float = 0.85

    w_count: float = 0.35
    w_strength: float = 0.45
    w_patterns: float = 0.20


# =========================
# HELPERS
# =========================

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x or 0.0)))


def _safe_signal_score(v: Any) -> float:
    try:
        return float((v or {}).get("score", 0.0) or 0.0)
    except Exception:
        return 0.0


def _risk_rank(risk: str) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(str(risk), 0)


def compute_confidence_score(
    *,
    signal_scores: Mapping[str, float],
    patterns: List[PatternMatch],
    cfg: ConfidenceScoreConfig,
) -> float:
    scores: List[float] = []

    for v in (signal_scores or {}).values():
        try:
            scores.append(float(v or 0.0))
        except Exception:
            scores.append(0.0)

    scores.sort(reverse=True)

    support_count = sum(1 for s in scores if s >= cfg.support_threshold)
    elevated_count = sum(1 for s in scores if s >= cfg.elevated_threshold)
    strong_count = sum(1 for s in scores if s >= cfg.strong_threshold)

    count_term = min(
        1.0,
        (0.20 * support_count + 0.35 * elevated_count + 0.45 * strong_count) / 2.0,
    )

    strength_term = sum(scores[:3]) / len(scores[:3]) if scores else 0.0
    pattern_term = max((float(p.strength) for p in (patterns or [])), default=0.0)

    conf = (
        cfg.w_count * count_term
        + cfg.w_strength * strength_term
        + cfg.w_patterns * pattern_term
    )

    return _clamp01(conf)


def _predict_ml_safely(features: Features) -> Dict[str, Any]:
    try:
        return predict_risk(features)
    except Exception as e:
        return {
            "risk": "LOW",
            "label": 0,
            "probabilities": {},
            "confidence": 0.0,
            "error": str(e),
        }


# =========================
# MAIN PIPELINE
# =========================

def score_event_batch(
    raw_events: List[Dict[str, Any]],
    *,
    attempt_id: Optional[str] = None,
    cfg: Optional[ScoringConfig] = None,
    pattern_cfg: Optional[PatternConfig] = None,
    evidence_conf_cfg: Optional[ConfidenceScoreConfig] = None,
) -> RiskResult:

    scoring_cfg = cfg or ScoringConfig()

    patt_cfg = pattern_cfg or PatternConfig(
        min_idle_spike_s=scoring_cfg.feature.idle_spike_s,
        fast_answer_s=scoring_cfg.feature.fast_question_s,
    )

    ev_conf_cfg = evidence_conf_cfg or ConfidenceScoreConfig(
        support_threshold=scoring_cfg.reasoning.support_threshold,
        elevated_threshold=scoring_cfg.reasoning.elevated_threshold,
        strong_threshold=scoring_cfg.reasoning.strong_threshold,
    )

    # STEP 1: Normalize
    processed = normalize_events(raw_events, attempt_id=attempt_id)

    # STEP 2: Features
    features: Features = build_features(processed.events, scoring_cfg.feature)

    # STEP 3: Signals
    ws: WeightedSignals = detect_signals(features, scoring_cfg.signal)
    signals = ws.signals or {}
    scores = ws.scores or {}

    # STEP 4: Combine
    combined = combine_signals(signals, scoring_cfg.reasoning) or {}

    # STEP 5: Patterns
    patterns = detect_patterns(
        processed.events,
        features=features,
        signals=signals,
        cfg=patt_cfg,
    ) or []

    # =========================
    # ML PREDICTION
    # =========================

    ml_result = _predict_ml_safely(features)
    ml_risk = str(ml_result.get("risk", "LOW"))
    ml_confidence = float(ml_result.get("confidence") or 0.0)

    # =========================
    # LOW DATA SAFEGUARD
    # =========================

    questions = int(features.get("questions_seen", 0) or 0)
    has_signal = any(_safe_signal_score(v) > 0.2 for v in signals.values())
    has_pattern = len(patterns) > 0

    if questions == 0 and not has_signal and not has_pattern:
        result = RiskResult(
            attempt_id=processed.attempt_id,
            risk="LOW",
            confidence=0.1,
            confidence_score=0.1,
            promoted_by_pattern=False,
            base_score=0.0,
            combined_score=0.0,
            signals=signals,
            patterns=patterns,
            explanation={
                "summary": [
                    "Risk=LOW.",
                    "Insufficient behavioral data for reliable analysis.",
                ],
                "reasons": [
                    "Too few interactions detected and no suspicious signals.",
                ],
            },
            explanation_text="Insufficient behavioral data for reliable analysis.",
        )

        try:
            log_attempt(result=result, features=features)
        except Exception:
            pass

        return result

    # =========================
    # RULE RISK DECISION
    # =========================

    rule_risk = assign_risk(
        float(combined.get("combined_score", 0.0)),
        combined,
        scoring_cfg.risk,
    )

    promoted_by_pattern = False

    if rule_risk == "LOW" and patterns:
        if any(float(p.strength) >= 0.6 for p in patterns):
            rule_risk = "MEDIUM"
            promoted_by_pattern = True

    # =========================
    # HYBRID RULE + ML DECISION
    # =========================

    final_risk = rule_risk

    # Rules dominate obvious HIGH cases
    if rule_risk == "HIGH":
        final_risk = "HIGH"

    # ML can promote LOW to MEDIUM if confident
    elif rule_risk == "LOW" and ml_risk == "MEDIUM" and ml_confidence >= 0.60:
        final_risk = "MEDIUM"
        promoted_by_pattern = True

    # ML can promote MEDIUM to HIGH only when very confident and rule evidence exists
    elif (
        rule_risk == "MEDIUM"
        and ml_risk == "HIGH"
        and ml_confidence >= 0.85
        and (
            int(combined.get("elevated_count", 0) or 0) >= 1
            or int(combined.get("strong_count", 0) or 0) >= 1
            or len(patterns) > 0
        )
    ):
        final_risk = "HIGH"
        promoted_by_pattern = True

    # ML can keep/support MEDIUM
    elif rule_risk == "MEDIUM":
        final_risk = "MEDIUM"

    risk = final_risk

    # =========================
    # CONFIDENCE
    # =========================

    rule_confidence = compute_confidence(features, signals, combined, scoring_cfg.confidence)

    evidence_confidence_score = compute_confidence_score(
        signal_scores=scores,
        patterns=patterns,
        cfg=ev_conf_cfg,
    )

    # Blend evidence confidence and ML confidence conservatively
    if ml_confidence > 0:
        confidence_score = _clamp01(0.70 * evidence_confidence_score + 0.30 * ml_confidence)
    else:
        confidence_score = evidence_confidence_score

    confidence = _clamp01(rule_confidence)

    # =========================
    # EXPLANATION
    # =========================

    explanation = generate_explanations(features, signals, risk, patterns) or {}

    explanation.setdefault("summary", [])
    explanation.setdefault("reasons", [])

    explanation["summary"].append(
        f"ML prediction={ml_risk}."
    )

    if ml_confidence:
        explanation["summary"].append(
            f"ML confidence={ml_confidence:.2f}."
        )

    explanation["reasons"].append(
        f"Hybrid decision used rule risk={rule_risk} and ML risk={ml_risk}."
    )

    explanation_text = (
        " ".join(explanation.get("summary", [])) + " "
        + " ".join(explanation.get("reasons", []))
    ).strip()

    # =========================
    # FINAL RESULT
    # =========================

    result = RiskResult(
        attempt_id=processed.attempt_id,
        risk=risk,
        confidence=float(confidence or 0.0),
        confidence_score=float(confidence_score or 0.0),
        promoted_by_pattern=promoted_by_pattern,
        base_score=float(combined.get("base_score", 0.0)),
        combined_score=float(combined.get("combined_score", 0.0)),
        signals=signals,
        patterns=patterns,
        explanation=explanation,
        explanation_text=explanation_text,
    )

    try:
        log_attempt(result=result, features=features)
    except Exception:
        pass

    return result