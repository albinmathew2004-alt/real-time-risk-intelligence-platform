"""Phase 23 — MEDIUM Refinement Layer (V2 Hybrid ML Refinement).

Rules:
- The rule engine remains the primary decision-maker.
- ML refinement MUST NOT create HIGH decisions.
- ML refinement MUST NOT override HIGH decisions.
- ML only refines borderline LOW/MEDIUM.

This module exposes:
  refine_low_medium_risk(features: dict, current_risk: str, combined_score: float)

The function returns:
  (refined_risk: str, note: str, ml_medium_probability: float | None)

How it integrates:
- In engine/core/risk_engine.py, after rule risk is computed:
  - if rule risk is HIGH: keep HIGH
  - else: call this function to allow LOW↔MEDIUM refinement
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# Files created by engine/ml/train_logistic_model.py
MODEL_PATH = Path(__file__).resolve().parent / "models" / "logistic_model.pkl"
SCALER_PATH = Path(__file__).resolve().parent / "models" / "scaler.pkl"


FEATURE_COLUMNS = [
    # Base features already produced by engine/core/feature_engineering.py
    "paste_count",
    "tab_hidden_count",
    "idle_spike_count",
    "time_per_question_mean_s",
    "questions_seen",
    "attempt_duration_s",

    # Derived features (computed in both training and inference)
    "paste_per_question",
    "tab_per_question",
    "speed",
    "tab_density",
    "paste_density",
    "idle_ratio",
    "suspicious_combo",
    "fast_paste",
    "fast_tab",

    # Phase 26 — advanced behavioral features (computed in engine/core/feature_engineering.py)
    "suspicious_sequence_count",
    "answer_burst_count",
    "tab_return_fast_answer_count",
    "paste_to_answer_seconds_min",
    "paste_to_answer_fast_count",
    "navigation_revisit_count",
    "question_jump_count",
    "timing_variance_score",
    "behavior_entropy_score",
    "idle_to_activity_burst_count",
]


@dataclass
class _LoadedArtifacts:
    model: Any
    scaler: Any


_ARTIFACTS: Optional[_LoadedArtifacts] = None


def _try_load_artifacts() -> Optional[_LoadedArtifacts]:
    """Load model/scaler once and cache.

    Returns None if artifacts are missing or dependencies aren’t installed.
    """

    global _ARTIFACTS
    if _ARTIFACTS is not None:
        return _ARTIFACTS

    if not MODEL_PATH.exists() or not SCALER_PATH.exists():
        return None

    try:
        import joblib
    except Exception:
        return None

    try:
        model = joblib.load(MODEL_PATH)
        scaler = joblib.load(SCALER_PATH)
    except Exception:
        return None

    _ARTIFACTS = _LoadedArtifacts(model=model, scaler=scaler)
    return _ARTIFACTS


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _build_feature_row(features: Dict[str, Any]) -> Dict[str, float]:
    """Convert engine feature dict into a flat numeric row for ML."""

    paste = _to_float(features.get("paste_count", 0.0))
    tab = _to_float(features.get("tab_hidden_count", 0.0))
    idle = _to_float(features.get("idle_spike_count", 0.0))

    questions = max(1.0, _to_float(features.get("questions_seen", 1.0)))
    duration_s = max(1.0, _to_float(features.get("attempt_duration_s", 1.0)))
    time_per_q = max(0.001, _to_float(features.get("time_per_question_mean_s", 1.0)))

    paste_per_q = paste / questions
    tab_per_q = tab / questions

    # Smaller time-per-question -> higher speed
    speed = 1.0 / (time_per_q + 1e-5)

    tab_density = tab / (duration_s + 1e-5)
    paste_density = paste / (duration_s + 1e-5)
    idle_ratio = idle / questions

    suspicious_combo = paste_per_q * tab_per_q
    fast_paste = speed * paste_per_q
    fast_tab = speed * tab_per_q

    # Phase 26 features: safe defaults if missing
    suspicious_sequence_count = _to_float(features.get("suspicious_sequence_count", 0.0))
    answer_burst_count = _to_float(features.get("answer_burst_count", 0.0))
    tab_return_fast_answer_count = _to_float(features.get("tab_return_fast_answer_count", 0.0))

    # Sentinel is 999.0 when no paste→answer pair exists.
    paste_to_answer_seconds_min = _to_float(features.get("paste_to_answer_seconds_min", 999.0), default=999.0)
    paste_to_answer_fast_count = _to_float(features.get("paste_to_answer_fast_count", 0.0))
    navigation_revisit_count = _to_float(features.get("navigation_revisit_count", 0.0))
    question_jump_count = _to_float(features.get("question_jump_count", 0.0))
    timing_variance_score = _to_float(features.get("timing_variance_score", 0.0))
    behavior_entropy_score = _to_float(features.get("behavior_entropy_score", 0.0))
    idle_to_activity_burst_count = _to_float(features.get("idle_to_activity_burst_count", 0.0))

    row = {
        "paste_count": paste,
        "tab_hidden_count": tab,
        "idle_spike_count": idle,
        "time_per_question_mean_s": time_per_q,
        "questions_seen": questions,
        "attempt_duration_s": duration_s,
        "paste_per_question": paste_per_q,
        "tab_per_question": tab_per_q,
        "speed": speed,
        "tab_density": tab_density,
        "paste_density": paste_density,
        "idle_ratio": idle_ratio,
        "suspicious_combo": suspicious_combo,
        "fast_paste": fast_paste,
        "fast_tab": fast_tab,

        # Phase 26 advanced features
        "suspicious_sequence_count": suspicious_sequence_count,
        "answer_burst_count": answer_burst_count,
        "tab_return_fast_answer_count": tab_return_fast_answer_count,
        "paste_to_answer_seconds_min": paste_to_answer_seconds_min,
        "paste_to_answer_fast_count": paste_to_answer_fast_count,
        "navigation_revisit_count": navigation_revisit_count,
        "question_jump_count": question_jump_count,
        "timing_variance_score": timing_variance_score,
        "behavior_entropy_score": behavior_entropy_score,
        "idle_to_activity_burst_count": idle_to_activity_burst_count,
    }

    return row


def _predict_medium_probability(features: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """Predict P(MEDIUM) using the logistic regression model.

    The model is trained ONLY on LOW/MEDIUM.
    """

    artifacts = _try_load_artifacts()
    if artifacts is None:
        return None, "ML artifacts missing (train_logistic_model.py not run)"

    row = _build_feature_row(features)

    # Build X in the trained column order.
    X = [[row.get(col, 0.0) for col in FEATURE_COLUMNS]]

    try:
        Xs = artifacts.scaler.transform(X)
    except Exception:
        # If scaler fails for any reason, keep the current risk.
        return None, "ML scaler failed; skipping refinement"

    model = artifacts.model

    # scikit-learn LogisticRegression exposes predict_proba.
    if not hasattr(model, "predict_proba"):
        return None, "ML model missing predict_proba; skipping refinement"

    try:
        probs = model.predict_proba(Xs)[0]
        classes = list(model.classes_)
    except Exception:
        return None, "ML prediction failed; skipping refinement"

    # Training uses labels: 0=LOW, 1=MEDIUM.
    if 1 in classes:
        idx = classes.index(1)
        return float(probs[idx]), "ok"

    # Fall back: if MEDIUM is encoded as 1-like string.
    if "MEDIUM" in classes:
        idx = classes.index("MEDIUM")
        return float(probs[idx]), "ok"

    return None, "ML classes did not include MEDIUM; skipping refinement"


def refine_low_medium_risk(
    features: Dict[str, Any],
    current_risk: str,
    combined_score: float,
) -> Tuple[str, str, Optional[float]]:
    """Refine ONLY LOW/MEDIUM using logistic regression.

    Returns (refined_risk, note, p_medium).

    Safety rules enforced:
    - If current risk is HIGH: return HIGH immediately.
    - The function never returns HIGH for non-HIGH inputs.

    The thresholds below are intentionally conservative to preserve low false positives.
    """

    current = str(current_risk or "").upper().strip()

    if current == "HIGH":
        return "HIGH", "Rule risk is HIGH; ML refinement skipped.", None

    if current not in ("LOW", "MEDIUM"):
        return current_risk, "Unknown current risk; ML refinement skipped.", None

    # Only refine borderline cases.
    # (This keeps ML from flipping clearly safe or clearly suspicious attempts.)
    try:
        cs = float(combined_score or 0.0)
    except Exception:
        cs = 0.0

    # Phase 26 note:
    # The rule-engine combined_score is intentionally conservative and may under-react
    # to subtle *sequential* behaviors (e.g., "idle → tab away → paste → fast answer").
    # We therefore allow ML refinement to run for LOW cases with clear advanced
    # suspicious evidence even when combined_score is low.

    suspicious_seq = _to_float(features.get("suspicious_sequence_count", 0.0))
    paste_fast = _to_float(features.get("paste_to_answer_fast_count", 0.0))
    tab_return_fast = _to_float(features.get("tab_return_fast_answer_count", 0.0))
    answer_bursts = _to_float(features.get("answer_burst_count", 0.0))
    idle_bursts = _to_float(features.get("idle_to_activity_burst_count", 0.0))

    advanced_suspicion = (
        suspicious_seq > 0
        or paste_fast > 0
        or tab_return_fast > 0
        or answer_bursts > 0
        or idle_bursts > 0
    )

    if current == "LOW" and cs < 0.20 and not advanced_suspicion:
        return "LOW", "Combined score far below MEDIUM; ML refinement skipped.", None

    if current == "MEDIUM" and cs > 0.70:
        return "MEDIUM", "Combined score strongly MEDIUM; ML refinement skipped.", None

    p_medium, status = _predict_medium_probability(features)
    if p_medium is None:
        return current, f"ML refinement unavailable: {status}.", None

    # Extra guardrails to preserve low false positives:
    paste = _to_float(features.get("paste_count", 0.0))
    tab = _to_float(features.get("tab_hidden_count", 0.0))
    time_per_q = _to_float(features.get("time_per_question_mean_s", 0.0))

    has_any_suspicion = (paste > 0) or (tab > 0) or (0.0 < time_per_q < 12.0) or advanced_suspicion

    # Decision thresholds (tweakable):
    # If advanced sequential evidence exists, we can be slightly less strict.
    promote_threshold = 0.62 if advanced_suspicion else 0.65  # LOW -> MEDIUM
    demote_threshold = 0.35   # MEDIUM -> LOW

    if current == "LOW":
        min_cs = 0.15 if advanced_suspicion else 0.25

        if p_medium >= promote_threshold and has_any_suspicion and cs >= min_cs:
            return (
                "MEDIUM",
                f"ML refinement promoted LOW→MEDIUM (p_medium={p_medium:.2f}) for borderline suspicious behavior.",
                p_medium,
            )
        return (
            "LOW",
            f"ML refinement kept LOW (p_medium={p_medium:.2f}).",
            p_medium,
        )

    # current == MEDIUM
    if p_medium <= demote_threshold and cs <= 0.55:
        return (
            "LOW",
            f"ML refinement demoted MEDIUM→LOW (p_medium={p_medium:.2f}) due to weak suspicious evidence.",
            p_medium,
        )

    return (
        "MEDIUM",
        f"ML refinement kept MEDIUM (p_medium={p_medium:.2f}).",
        p_medium,
    )
