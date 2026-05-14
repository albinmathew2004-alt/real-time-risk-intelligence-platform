"""Phase 22 — Evaluation System: evaluate the risk engine on a labelled dataset.

This script:
- loads `data/eval_dataset.jsonl`
- runs the existing engine wrapper: `engine.core.scorer.run_scoring(events, attempt_id)`
- compares predicted vs expected (LOW | MEDIUM | HIGH)
- computes:
  - accuracy
  - false positive rate (expected LOW but predicted MEDIUM/HIGH)
  - HIGH precision
  - HIGH recall
  - MEDIUM recall
  - confusion matrix
  - risk distribution
- prints a clear console summary
- saves:
  - `data/eval_results.json`
  - `data/eval_confusion_matrix.csv`

How to run (from repo root, Windows PowerShell):
  python scripts/evaluate_risk_engine.py

Tip: If you want fresh artifacts end-to-end:
  python scripts/generate_eval_dataset.py
  python scripts/evaluate_risk_engine.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Tuple

# Allow running this script directly from Windows PowerShell even if the
# current working directory isn't the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.core.scorer import run_scoring


RiskLabel = Literal["LOW", "MEDIUM", "HIGH"]


LABELS: Tuple[RiskLabel, RiskLabel, RiskLabel] = ("LOW", "MEDIUM", "HIGH")


def _safe_label(x: Any) -> RiskLabel:
    s = str(x or "").upper().strip()
    if s in LABELS:
        return s  # type: ignore[return-value]
    return "LOW"


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_no} in {path}: {e}")

    return records


def _confusion_matrix_init() -> Dict[RiskLabel, Dict[RiskLabel, int]]:
    return {exp: {pred: 0 for pred in LABELS} for exp in LABELS}


def _confusion_add(cm: Dict[RiskLabel, Dict[RiskLabel, int]], expected: RiskLabel, predicted: RiskLabel) -> None:
    cm[expected][predicted] += 1


def _accuracy(cm: Dict[RiskLabel, Dict[RiskLabel, int]]) -> float:
    total = sum(cm[e][p] for e in LABELS for p in LABELS)
    correct = sum(cm[l][l] for l in LABELS)
    return (correct / total) if total else 0.0


def _false_positive_rate(cm: Dict[RiskLabel, Dict[RiskLabel, int]]) -> float:
    """False positive rate for 'suspicious' detection.

    Definition used here (simple + explainable):
      expected LOW but predicted MEDIUM/HIGH
    """

    expected_low = sum(cm["LOW"][p] for p in LABELS)
    if expected_low == 0:
        return 0.0

    fp = cm["LOW"]["MEDIUM"] + cm["LOW"]["HIGH"]
    return fp / expected_low


def _precision_high(cm: Dict[RiskLabel, Dict[RiskLabel, int]]) -> float:
    predicted_high = sum(cm[e]["HIGH"] for e in LABELS)
    if predicted_high == 0:
        return 0.0
    tp = cm["HIGH"]["HIGH"]
    return tp / predicted_high


def _recall(cm: Dict[RiskLabel, Dict[RiskLabel, int]], label: RiskLabel) -> float:
    expected = sum(cm[label][p] for p in LABELS)
    if expected == 0:
        return 0.0
    tp = cm[label][label]
    return tp / expected


def _risk_distribution(predicted_labels: Iterable[RiskLabel]) -> Dict[RiskLabel, int]:
    dist = {l: 0 for l in LABELS}
    for p in predicted_labels:
        dist[_safe_label(p)] += 1
    return dist


def _format_pct(x: float) -> str:
    return f"{(100.0 * x):6.2f}%"


def _print_summary(*, metrics: Dict[str, Any], cm: Dict[RiskLabel, Dict[RiskLabel, int]]) -> None:
    print("\n=== Risk Engine Evaluation Summary ===\n")

    print(f"Attempts: {metrics['n_attempts']}")
    print(f"Accuracy: {_format_pct(metrics['accuracy'])}")
    print(f"False Positive Rate (LOW→MED/HIGH): {_format_pct(metrics['false_positive_rate'])}")
    print(f"HIGH precision: {_format_pct(metrics['high_precision'])}")
    print(f"HIGH recall: {_format_pct(metrics['high_recall'])}")
    print(f"MEDIUM recall: {_format_pct(metrics['medium_recall'])}")

    dist = metrics.get("predicted_distribution", {})
    print("\nPredicted risk distribution:")
    print(f"  LOW={dist.get('LOW', 0)} MEDIUM={dist.get('MEDIUM', 0)} HIGH={dist.get('HIGH', 0)}")

    print("\nConfusion matrix (rows=expected, cols=predicted):")
    header = "           LOW   MED   HIGH   TOTAL"
    print(header)
    for exp in LABELS:
        row = cm[exp]
        total = sum(row[p] for p in LABELS)
        print(
            f"{exp:>7}  "
            f"{row['LOW']:>6}"
            f"{row['MEDIUM']:>6}"
            f"{row['HIGH']:>7}"
            f"{total:>8}"
        )

    total_all = sum(cm[e][p] for e in LABELS for p in LABELS)
    print(f"\nTotal: {total_all}")


def _write_confusion_csv(path: Path, cm: Dict[RiskLabel, Dict[RiskLabel, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["expected\\predicted", "LOW", "MEDIUM", "HIGH", "total"])
        for exp in LABELS:
            row = cm[exp]
            total = sum(row[p] for p in LABELS)
            w.writerow([exp, row["LOW"], row["MEDIUM"], row["HIGH"], total])

        # Column totals
        col_totals = {
            "LOW": sum(cm[e]["LOW"] for e in LABELS),
            "MEDIUM": sum(cm[e]["MEDIUM"] for e in LABELS),
            "HIGH": sum(cm[e]["HIGH"] for e in LABELS),
        }
        grand_total = sum(col_totals.values())
        w.writerow(["TOTAL", col_totals["LOW"], col_totals["MEDIUM"], col_totals["HIGH"], grand_total])


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the risk engine on a labelled JSONL dataset")
    parser.add_argument("--dataset", default=str(Path("data") / "eval_dataset.jsonl"), help="Path to eval_dataset.jsonl")
    parser.add_argument("--out_json", default=str(Path("data") / "eval_results.json"), help="Output JSON results path")
    parser.add_argument("--out_cm_csv", default=str(Path("data") / "eval_confusion_matrix.csv"), help="Output confusion matrix CSV path")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    out_json = Path(args.out_json)
    out_cm_csv = Path(args.out_cm_csv)

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {dataset_path}. Run: python scripts/generate_eval_dataset.py"
        )

    records = _load_jsonl(dataset_path)

    cm = _confusion_matrix_init()
    per_attempt: List[Dict[str, Any]] = []
    predicted_labels: List[RiskLabel] = []

    for rec in records:
        attempt_id = str(rec.get("attempt_id") or "")
        expected = _safe_label(rec.get("expected_label"))
        events = rec.get("events") or []

        if not attempt_id:
            continue

        # Call your existing scoring wrapper (do not change engine logic).
        result = run_scoring(events, attempt_id)

        predicted = _safe_label(result.get("risk"))
        predicted_labels.append(predicted)
        _confusion_add(cm, expected, predicted)

        per_attempt.append(
            {
                "attempt_id": attempt_id,
                "expected_label": expected,
                "predicted_label": predicted,
                "confidence": result.get("confidence"),
                "combined_score": result.get("combined_score"),
                "explanation": result.get("explanation"),
                "is_edge_case": bool(rec.get("is_edge_case")),
                "edge_case_scenario": rec.get("edge_case_scenario"),
            }
        )

    metrics: Dict[str, Any] = {
        "n_attempts": len(per_attempt),
        "accuracy": _accuracy(cm),
        "false_positive_rate": _false_positive_rate(cm),
        "high_precision": _precision_high(cm),
        "high_recall": _recall(cm, "HIGH"),
        "medium_recall": _recall(cm, "MEDIUM"),
        "predicted_distribution": _risk_distribution(predicted_labels),
        "labels": list(LABELS),
    }

    results_obj: Dict[str, Any] = {
        "dataset": {
            "path": str(dataset_path.as_posix()),
            "n_records": len(records),
        },
        "metrics": metrics,
        "confusion_matrix": cm,
        "per_attempt": per_attempt,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(results_obj, f, indent=2)

    _write_confusion_csv(out_cm_csv, cm)

    _print_summary(metrics=metrics, cm=cm)

    print("\nSaved artifacts:")
    print(f"  - {out_json}")
    print(f"  - {out_cm_csv}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
