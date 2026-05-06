import joblib
import pandas as pd
from pathlib import Path

MODEL_PATH = Path("models/risk_model.pkl")

_model = None
_scaler = None


FEATURE_COLUMNS = [
    "paste_count",
    "tab_hidden_count",
    "idle_spike_count",
    "time_per_question",
    "questions_seen",
    "attempt_duration",
    "paste_per_question",
    "tab_per_question",
    "speed",
    "tab_density",
    "paste_density",
    "idle_ratio",
    "suspicious_combo",
    "fast_paste",
    "fast_tab",
]


def load_model():
    global _model, _scaler

    if _model is None:
        loaded = joblib.load(MODEL_PATH)

        # Your train_model.py saves: joblib.dump((model, scaler), MODEL_FILE)
        if isinstance(loaded, tuple):
            _model, _scaler = loaded
        else:
            _model = loaded
            _scaler = None

    return _model, _scaler


def extract_ml_features(features: dict) -> pd.DataFrame:
    paste = float(features.get("paste_count", 0) or 0)
    tab = float(features.get("tab_hidden_count", 0) or 0)
    idle = float(features.get("idle_spike_count", 0) or 0)

    questions = float(features.get("questions_seen", 1) or 1)
    duration = float(features.get("attempt_duration_s", 1) or 1)
    time_per_q = float(features.get("time_per_question_mean_s", 1) or 1)

    paste_per_q = paste / questions
    tab_per_q = tab / questions
    speed = 1 / (time_per_q + 1e-5)

    tab_density = tab / (duration + 1e-5)
    paste_density = paste / (duration + 1e-5)
    idle_ratio = idle / questions

    suspicious_combo = paste_per_q * tab_per_q
    fast_paste = speed * paste_per_q
    fast_tab = speed * tab_per_q

    row = {
        "paste_count": paste,
        "tab_hidden_count": tab,
        "idle_spike_count": idle,
        "time_per_question": time_per_q,
        "questions_seen": questions,
        "attempt_duration": duration,
        "paste_per_question": paste_per_q,
        "tab_per_question": tab_per_q,
        "speed": speed,
        "tab_density": tab_density,
        "paste_density": paste_density,
        "idle_ratio": idle_ratio,
        "suspicious_combo": suspicious_combo,
        "fast_paste": fast_paste,
        "fast_tab": fast_tab,
    }

    return pd.DataFrame([row], columns=FEATURE_COLUMNS)


def predict_risk(features: dict) -> dict:
    model, scaler = load_model()

    X = extract_ml_features(features)

    if scaler is not None:
        X_input = scaler.transform(X)
    else:
        X_input = X

    pred = int(model.predict(X_input)[0])

    risk_map = {
        0: "LOW",
        1: "MEDIUM",
        2: "HIGH",
    }

    probabilities = {}

    if hasattr(model, "predict_proba"):
        probs = model.predict_proba(X_input)[0]
        for cls, prob in zip(model.classes_, probs):
            probabilities[risk_map.get(int(cls), str(cls))] = float(prob)

    return {
        "risk": risk_map.get(pred, "LOW"),
        "label": pred,
        "probabilities": probabilities,
        "confidence": max(probabilities.values()) if probabilities else None,
    }