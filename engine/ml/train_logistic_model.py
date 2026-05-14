"""Phase 23 — Train Logistic Regression refinement model (LOW vs MEDIUM).

This script trains a *refinement* model used ONLY for LOW↔MEDIUM decisions.
It does NOT train HIGH and MUST NOT be used to create HIGH decisions.

Outputs:
- engine/ml/models/logistic_model.pkl
- engine/ml/models/scaler.pkl

Dataset:
- data/ml_training_dataset.csv (labels: LOW, MEDIUM)

How to run (from repo root, Windows PowerShell):
  python engine/ml/train_logistic_model.py

If you want to regenerate the training dataset from data/eval_dataset.jsonl:
  python engine/ml/train_logistic_model.py --rebuild-dataset

Optional comparison (Random Forest):
  python engine/ml/train_logistic_model.py --rf
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


# Allow running directly from Windows PowerShell regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from engine.core.event_processor import normalize_events
from engine.core.feature_engineering import build_features
from engine.core.types import FeatureConfig


DATASET_PATH_DEFAULT = REPO_ROOT / "data" / "ml_training_dataset.csv"
EVAL_JSONL_DEFAULT = REPO_ROOT / "data" / "eval_dataset.jsonl"

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_PATH = MODEL_DIR / "logistic_model.pkl"
SCALER_PATH = MODEL_DIR / "scaler.pkl"


FEATURE_COLUMNS = [
    "paste_count",
    "tab_hidden_count",
    "idle_spike_count",
    "time_per_question_mean_s",
    "questions_seen",
    "attempt_duration_s",
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


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _build_feature_row(engine_features: Dict[str, Any]) -> Dict[str, float]:
    paste = _to_float(engine_features.get("paste_count", 0.0))
    tab = _to_float(engine_features.get("tab_hidden_count", 0.0))
    idle = _to_float(engine_features.get("idle_spike_count", 0.0))

    questions = max(1.0, _to_float(engine_features.get("questions_seen", 1.0)))
    duration_s = max(1.0, _to_float(engine_features.get("attempt_duration_s", 1.0)))
    time_per_q = max(0.001, _to_float(engine_features.get("time_per_question_mean_s", 1.0)))

    paste_per_q = paste / questions
    tab_per_q = tab / questions
    speed = 1.0 / (time_per_q + 1e-5)

    tab_density = tab / (duration_s + 1e-5)
    paste_density = paste / (duration_s + 1e-5)
    idle_ratio = idle / questions

    suspicious_combo = paste_per_q * tab_per_q
    fast_paste = speed * paste_per_q
    fast_tab = speed * tab_per_q

    # Phase 26 features: safe defaults if missing
    suspicious_sequence_count = _to_float(engine_features.get("suspicious_sequence_count", 0.0))
    answer_burst_count = _to_float(engine_features.get("answer_burst_count", 0.0))
    tab_return_fast_answer_count = _to_float(engine_features.get("tab_return_fast_answer_count", 0.0))

    # Sentinel is 999.0 when no paste→answer pair exists.
    paste_to_answer_seconds_min = _to_float(engine_features.get("paste_to_answer_seconds_min", 999.0), default=999.0)
    paste_to_answer_fast_count = _to_float(engine_features.get("paste_to_answer_fast_count", 0.0))
    navigation_revisit_count = _to_float(engine_features.get("navigation_revisit_count", 0.0))
    question_jump_count = _to_float(engine_features.get("question_jump_count", 0.0))
    timing_variance_score = _to_float(engine_features.get("timing_variance_score", 0.0))
    behavior_entropy_score = _to_float(engine_features.get("behavior_entropy_score", 0.0))
    idle_to_activity_burst_count = _to_float(engine_features.get("idle_to_activity_burst_count", 0.0))

    return {
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


def _label_to_int(label: str) -> int:
    s = str(label or "").upper().strip()
    if s == "MEDIUM":
        return 1
    return 0


def _int_to_label(y: int) -> str:
    return "MEDIUM" if int(y) == 1 else "LOW"


def _load_csv_dataset(path: Path) -> Tuple[List[List[float]], List[int]]:
    X: List[List[float]] = []
    y: List[int] = []

    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise ValueError(f"Empty CSV header: {path}")

        for row in r:
            label = row.get("label", "LOW")
            y.append(_label_to_int(label))

            X.append([_to_float(row.get(col, 0.0)) for col in FEATURE_COLUMNS])

    return X, y


def _write_csv_dataset(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["label"] + FEATURE_COLUMNS

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def _rebuild_from_eval_jsonl(eval_path: Path, out_csv: Path) -> int:
    """Build LOW/MEDIUM training data by featurizing eval attempts.

    HIGH attempts are ignored.
    """

    import json

    if not eval_path.exists():
        raise FileNotFoundError(
            f"Missing {eval_path}. Run: python scripts/generate_eval_dataset.py"
        )

    rows: List[Dict[str, Any]] = []

    cfg = FeatureConfig()

    with eval_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)

            label = str(rec.get("expected_label") or "").upper().strip()
            if label not in ("LOW", "MEDIUM"):
                continue

            attempt_id = str(rec.get("attempt_id") or "")
            events = rec.get("events") or []

            processed = normalize_events(events, attempt_id=attempt_id)
            feats = build_features(processed.events, cfg)
            row = _build_feature_row(feats)
            row["label"] = label
            rows.append(row)

    if not rows:
        raise ValueError("No LOW/MEDIUM records found in eval dataset")

    _write_csv_dataset(out_csv, rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train LOW/MEDIUM logistic regression refinement model")
    parser.add_argument("--dataset", default=str(DATASET_PATH_DEFAULT), help="Training dataset CSV path")
    parser.add_argument("--eval_jsonl", default=str(EVAL_JSONL_DEFAULT), help="Eval JSONL to rebuild dataset from")
    parser.add_argument("--rebuild-dataset", action="store_true", help="Rebuild data/ml_training_dataset.csv from eval_dataset.jsonl")
    parser.add_argument("--rf", action="store_true", help="Also train a RandomForestClassifier for comparison")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    eval_jsonl = Path(args.eval_jsonl)

    if args.rebuild_dataset or (not dataset_path.exists()):
        n = _rebuild_from_eval_jsonl(eval_jsonl, dataset_path)
        print(f"Built training dataset: {dataset_path} (rows={n})")

    # Dependencies required by spec
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler

    try:
        import joblib
    except Exception as e:
        raise RuntimeError("joblib is required to save the model artifacts") from e

    X, y = _load_csv_dataset(dataset_path)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.25,
        random_state=23,
        stratify=y if len(set(y)) > 1 else None,
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    # Conservative class weights to reduce false positives (MEDIUM predicted too often)
    model = LogisticRegression(
        max_iter=200,
        class_weight={0: 1.0, 1: 1.25},
        solver="lbfgs",
    )

    model.fit(X_train_s, y_train)

    y_pred = model.predict(X_test_s)

    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1])

    print("\n=== Logistic Regression (LOW vs MEDIUM) ===")
    print(f"Dataset: {dataset_path}")
    print(f"Train size: {len(X_train)} | Test size: {len(X_test)}")
    print(f"Accuracy:  {acc:.3f}")
    print(f"Precision: {prec:.3f}  (MEDIUM as positive class)")
    print(f"Recall:    {rec:.3f}  (MEDIUM recall)")

    print("\nConfusion matrix (rows=true, cols=pred):")
    print("           pred_LOW  pred_MED")
    print(f"true_LOW     {cm[0][0]:>6}    {cm[0][1]:>6}")
    print(f"true_MED     {cm[1][0]:>6}    {cm[1][1]:>6}")

    if args.rf:
        from sklearn.ensemble import RandomForestClassifier

        rf = RandomForestClassifier(
            n_estimators=200,
            random_state=23,
            class_weight={0: 1.0, 1: 1.25},
        )
        rf.fit(X_train, y_train)
        y_pred_rf = rf.predict(X_test)

        acc_rf = accuracy_score(y_test, y_pred_rf)
        prec_rf = precision_score(y_test, y_pred_rf, zero_division=0)
        rec_rf = recall_score(y_test, y_pred_rf, zero_division=0)
        cm_rf = confusion_matrix(y_test, y_pred_rf, labels=[0, 1])

        print("\n=== Random Forest (comparison only) ===")
        print(f"Accuracy:  {acc_rf:.3f}")
        print(f"Precision: {prec_rf:.3f}")
        print(f"Recall:    {rec_rf:.3f}")
        print("\nConfusion matrix (rows=true, cols=pred):")
        print("           pred_LOW  pred_MED")
        print(f"true_LOW     {cm_rf[0][0]:>6}    {cm_rf[0][1]:>6}")
        print(f"true_MED     {cm_rf[1][0]:>6}    {cm_rf[1][1]:>6}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)

    print("\nSaved artifacts:")
    print(f"  - {MODEL_PATH}")
    print(f"  - {SCALER_PATH}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
