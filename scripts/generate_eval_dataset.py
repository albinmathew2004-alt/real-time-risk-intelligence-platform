"""Phase 25 — Large Scale Realistic Behavioral Dataset Engine.

This script generates a labelled JSONL dataset of *attempt-level* records for:
- ML training (LOW vs MEDIUM refinement)
- evaluation/stress testing
- behavioral realism testing

It preserves the existing event schema used by your scoring engine:
    {attempt_id, event_type, payload, occurred_at}

Output: `data/eval_dataset.jsonl` (default)

Examples (from repo root, Windows PowerShell):
    python scripts/generate_eval_dataset.py --attempts 1000
    python scripts/generate_eval_dataset.py --attempts 5000 --seed 22 --ratio-medium 0.55
    python scripts/generate_eval_dataset.py --attempts 1000 --ratio-edge-low 0.08 --print-samples 2

Notes:
- "edge LOW" attempts are still labelled LOW, but include tricky false-positive scenarios.
- MEDIUM attempts are intentionally abundant and ambiguous (most important for Phase 23).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Sequence


RiskLabel = Literal["LOW", "MEDIUM", "HIGH"]
Event = Dict[str, Any]


# Allow running this script directly from Windows PowerShell even if the
# current working directory isn't the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Import behavior profiles without requiring scripts/ to be a package.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from behavior_profiles import BehaviorEvent, profile_by_name


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str
    email: str
    cohort: str
    region: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    # Use a simple ISO-8601 form with 'Z' for UTC.
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_event(
    *,
    attempt_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: Dict[str, Any] | None = None,
    client_seq: int | None = None,
) -> Event:
    ev: Event = {
        "attempt_id": attempt_id,
        "event_type": event_type,
        "payload": payload or {},
        "occurred_at": _iso_z(occurred_at),
    }
    if client_seq is not None:
        ev["client_seq"] = int(client_seq)
    return ev


def _question_block(
    *,
    attempt_id: str,
    t: datetime,
    qid: str,
    duration_s: int,
    client_seq_start: int,
) -> tuple[List[Event], datetime, int]:
    """Emit enter/leave events so the feature engine can compute time-per-question."""

    ev: List[Event] = []
    ev.append(
        _build_event(
            attempt_id=attempt_id,
            event_type="question_view",
            occurred_at=t,
            payload={"question_id": qid, "action": "enter"},
            client_seq=client_seq_start,
        )
    )

    t2 = t + timedelta(seconds=int(duration_s))

    ev.append(
        _build_event(
            attempt_id=attempt_id,
            event_type="question_view",
            occurred_at=t2,
            payload={"question_id": qid, "action": "leave"},
            client_seq=client_seq_start + 1,
        )
    )

    return ev, t2, client_seq_start + 2


def _add_tab_hidden(events: List[Event], *, attempt_id: str, t: datetime, client_seq: int) -> int:
    events.append(
        _build_event(
            attempt_id=attempt_id,
            event_type="visibility_change",
            occurred_at=t,
            payload={"state": "hidden"},
            client_seq=client_seq,
        )
    )
    return client_seq + 1


def _add_paste(events: List[Event], *, attempt_id: str, t: datetime, client_seq: int) -> int:
    events.append(
        _build_event(
            attempt_id=attempt_id,
            event_type="clipboard",
            occurred_at=t,
            payload={"action": "paste"},
            client_seq=client_seq,
        )
    )
    return client_seq + 1


def _make_attempt_id(i: int) -> str:
    return f"eval_attempt_{i:06d}"


def _sample_candidate(i: int) -> Candidate:
    """Generate a larger variety of candidate identities.

    Keep it deterministic under `--seed` via the global random module.
    """

    first_names = [
        "Aarav",
        "Neha",
        "Rohan",
        "Ananya",
        "Vikram",
        "Meera",
        "Arjun",
        "Priya",
        "Kiran",
        "Sneha",
        "Ishaan",
        "Diya",
        "Kabir",
        "Nisha",
        "Rahul",
        "Sanya",
        "Aditya",
        "Pooja",
        "Suresh",
        "Maya",
    ]
    last_names = [
        "Sharma",
        "Patel",
        "Nair",
        "Iyer",
        "Menon",
        "Thomas",
        "Rao",
        "Das",
        "Joseph",
        "Kapoor",
        "Singh",
        "Gupta",
        "Bose",
        "Khan",
        "Chandra",
        "Mukherjee",
        "Naidu",
        "Reddy",
        "Mishra",
        "Jain",
    ]

    name = f"{random.choice(first_names)} {random.choice(last_names)}"
    candidate_id = f"candidate_{i:06d}"
    email = f"{candidate_id}@example.com"

    cohorts = ["2026A", "2026B", "2026C", "2026D"]
    regions = ["IN", "EU", "US", "LATAM"]

    return Candidate(
        candidate_id=candidate_id,
        name=name,
        email=email,
        cohort=random.choice(cohorts),
        region=random.choice(regions),
    )


def _question_ids(min_q: int, max_q: int) -> List[str]:
    n = max(3, random.randint(int(min_q), int(max_q)))
    return [f"q{i+1}" for i in range(n)]


def _events_from_behavior(
    *,
    attempt_id: str,
    t0: datetime,
    behavior: List[BehaviorEvent],
) -> List[Event]:
    """Convert BehaviorEvent (offset-based) into engine-consumable events."""

    events: List[Event] = []
    seq = 1

    events.append(_build_event(attempt_id=attempt_id, event_type="exam_started", occurred_at=t0, client_seq=seq))
    seq += 1

    for ev in sorted(behavior, key=lambda e: (float(e.offset_s), str(e.event_type))):
        occurred_at = t0 + timedelta(seconds=float(ev.offset_s))
        events.append(
            _build_event(
                attempt_id=attempt_id,
                event_type=str(ev.event_type),
                occurred_at=occurred_at,
                payload=dict(ev.payload or {}),
                client_seq=seq,
            )
        )
        seq += 1

    # Submit shortly after the last behavior event.
    last_offset = max([float(e.offset_s) for e in behavior], default=60.0)
    submit_at = t0 + timedelta(seconds=float(last_offset + random.uniform(8.0, 28.0)))
    events.append(_build_event(attempt_id=attempt_id, event_type="exam_submitted", occurred_at=submit_at, client_seq=seq))

    # Ensure stable chronological ordering.
    events.sort(key=lambda e: (e.get("occurred_at", ""), int(e.get("client_seq", 0))))
    return events


def _edge_case_behavior(*, question_ids: Sequence[str]) -> tuple[List[BehaviorEvent], str]:
    """LOW-labelled scenarios that can look suspicious.

    These help test the system's low-false-positive philosophy.
    """

    scenario = random.choice(
        [
            "developer_pastes_starter_code",
            "brief_docs_check",
            "accessibility_pause_heavy",
            "network_glitch_refresh_noise",
        ]
    )

    out: List[BehaviorEvent] = []
    t = 0.0

    # Cap tab-away events for LOW-labelled edge cases so they remain plausible
    # "almost false positives" rather than frequent deterministic HIGH triggers.
    docs_tab_budget = random.randint(1, 2)

    # A simple baseline: normal navigation with slow-ish timing.
    for idx, qid in enumerate(question_ids):
        dwell = random.uniform(22, 85)
        out.append(BehaviorEvent(offset_s=t, event_type="question_view", payload={"question_id": qid, "action": "enter"}))
        out.append(BehaviorEvent(offset_s=t + dwell, event_type="question_view", payload={"question_id": qid, "action": "leave"}))
        out.append(BehaviorEvent(offset_s=t + max(1.0, dwell - random.uniform(1.0, 8.0)), event_type="question_answer", payload={"question_id": qid, "answer": "selected_option"}))

        if scenario == "developer_pastes_starter_code":
            # One large paste early, but no tab away and timing remains normal.
            if idx == 0:
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(3.0, 10.0),
                        event_type="clipboard",
                        payload={"action": "paste", "size": "large", "source": "starter_code", "question_id": qid},
                    )
                )

        elif scenario == "brief_docs_check":
            # Occasional quick tab-away pair (docs), usually without paste.
            if docs_tab_budget > 0 and random.random() < 0.25:
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(2.0, 8.0),
                        event_type="visibility_change",
                        payload={"state": "hidden", "reason": "docs"},
                    )
                )
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(6.0, 16.0),
                        event_type="visibility_change",
                        payload={"state": "visible", "reason": "docs"},
                    )
                )
                docs_tab_budget -= 1

        elif scenario == "accessibility_pause_heavy":
            # A real accessibility pause or interruption. No paste/tab required.
            if random.random() < 0.35:
                idle_s = random.uniform(25, 140)
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(1.0, 6.0),
                        event_type="idle_state",
                        payload={"state": "idle", "duration_seconds": idle_s, "reason": "accessibility"},
                    )
                )
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(1.0, 6.0) + idle_s,
                        event_type="idle_state",
                        payload={"state": "active", "reason": "accessibility"},
                    )
                )

        elif scenario == "network_glitch_refresh_noise":
            # Extra enter/leave noise on the same question.
            if random.random() < 0.25:
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(4.0, 12.0),
                        event_type="question_view",
                        payload={"question_id": qid, "action": "enter", "reason": "refresh"},
                    )
                )
                out.append(
                    BehaviorEvent(
                        offset_s=t + random.uniform(14.0, 26.0),
                        event_type="question_view",
                        payload={"question_id": qid, "action": "leave", "reason": "refresh"},
                    )
                )

        t += dwell + random.uniform(1.5, 9.0)

    return out, scenario


def _label_from_bucket(bucket: str) -> RiskLabel:
    return "LOW" if bucket in ("LOW", "EDGE_LOW") else ("MEDIUM" if bucket == "MEDIUM" else "HIGH")


def _choose_profile_for_bucket(bucket: str) -> str:
    """Choose a profile name for a label bucket.

    Keep MEDIUM highly ambiguous by biasing toward borderline-ish profiles.
    """

    if bucket == "HIGH":
        return "AggressiveCheater"
    if bucket == "MEDIUM":
        return random.choices(
            [
                "BorderlineCandidate",
                "TemplateCopier",
                "SearcherPatternCandidate",
                "PanicSwitcherCandidate",
            ],
            weights=[0.42, 0.18, 0.22, 0.18],
            k=1,
        )[0]
    # LOW (non-edge)
    return random.choices(
        [
            "NormalThoughtfulCandidate",
            "FastButLegitCandidate",
            "DistractedButLegitCandidate",
            "BurnoutCandidate",
            "OverpreparedFastCandidate",
        ],
        weights=[0.35, 0.16, 0.18, 0.16, 0.15],
        k=1,
    )[0]


def generate_dataset(
    *,
    attempts: int,
    seed: int,
    ratio_low: float,
    ratio_medium: float,
    ratio_high: float,
    ratio_edge_low: float,
    min_questions: int,
    max_questions: int,
    minutes_between: float,
    days_back: int,
) -> List[Dict[str, Any]]:
    """Generate a large, realistic attempt dataset.

    The profile mix is designed so MEDIUM contains many ambiguous/borderline cases.
    """

    attempts = int(attempts)
    if attempts <= 0:
        return []

    random.seed(int(seed))

    ratio_low = float(ratio_low)
    ratio_medium = float(ratio_medium)
    ratio_high = float(ratio_high)
    ratio_edge_low = float(ratio_edge_low)

    if ratio_low < 0 or ratio_medium < 0 or ratio_high < 0 or ratio_edge_low < 0:
        raise ValueError("Ratios must be non-negative")

    ratio_sum = ratio_low + ratio_medium + ratio_high + ratio_edge_low
    if ratio_sum <= 0:
        raise ValueError("At least one ratio must be > 0")

    # If the user doesn't sum to 1.0, normalize conservatively.
    ratio_low /= ratio_sum
    ratio_medium /= ratio_sum
    ratio_high /= ratio_sum
    ratio_edge_low /= ratio_sum

    n_low = int(round(attempts * ratio_low))
    n_medium = int(round(attempts * ratio_medium))
    n_high = int(round(attempts * ratio_high))
    n_edge = max(0, attempts - (n_low + n_medium + n_high))

    # If rounding took edge cases away, restore them.
    if n_edge < int(round(attempts * ratio_edge_low)):
        n_edge = int(round(attempts * ratio_edge_low))

    # Re-balance medium to hit total exactly (MEDIUM is the primary target).
    n_medium = max(0, attempts - (n_low + n_high + n_edge))

    buckets: List[str] = (
        ["LOW"] * n_low
        + ["MEDIUM"] * n_medium
        + ["HIGH"] * n_high
        + ["EDGE_LOW"] * n_edge
    )
    random.shuffle(buckets)

    base_time = _utc_now() - timedelta(days=int(days_back))
    step = float(minutes_between)
    if step <= 0:
        step = 5.0

    records: List[Dict[str, Any]] = []

    for i, bucket in enumerate(buckets, start=1):
        # Per-attempt deterministic randomness for diversity with stable `--seed`.
        random.seed(int(seed) + i)

        attempt_id = _make_attempt_id(i)
        cand = _sample_candidate(i)
        expected = _label_from_bucket(bucket)

        t0 = base_time + timedelta(minutes=step * i)

        qids = _question_ids(int(min_questions), int(max_questions))

        is_edge_case = (bucket == "EDGE_LOW")
        edge_scenario = None

        if is_edge_case:
            behavior, edge_scenario = _edge_case_behavior(question_ids=qids)
            profile_name = "EdgeCaseLOW"
        else:
            profile_name = _choose_profile_for_bucket(bucket)
            profile = profile_by_name(profile_name)
            behavior = profile.generate(question_ids=qids)

        events = _events_from_behavior(attempt_id=attempt_id, t0=t0, behavior=behavior)

        record: Dict[str, Any] = {
            "attempt_id": attempt_id,
            "expected_label": expected,
            "candidate": {
                "candidate_id": cand.candidate_id,
                "name": cand.name,
                "email": cand.email,
                "cohort": cand.cohort,
                "region": cand.region,
            },
            "assessment": {
                "assessment_id": "assessment_python_01",
                "assessment_name": "Python Coding Assessment",
            },
            "events": events,
            "profile": profile_name,
            "is_edge_case": bool(is_edge_case),
        }

        if edge_scenario:
            record["edge_case_scenario"] = str(edge_scenario)

        records.append(record)

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a labelled risk-engine evaluation dataset (JSONL).")
    parser.add_argument("--out", default=str(Path("data") / "eval_dataset.jsonl"), help="Output JSONL path")
    parser.add_argument("--attempts", type=int, default=60, help="Number of attempts to generate")
    parser.add_argument("--n", type=int, default=None, help="Alias for --attempts (backwards compatible)")
    parser.add_argument("--seed", type=int, default=22, help="Random seed")
    parser.add_argument("--ratio-low", type=float, default=0.28, help="Fraction of attempts that are LOW (non-edge)")
    parser.add_argument("--ratio-medium", type=float, default=0.52, help="Fraction of attempts that are MEDIUM (borderline-heavy)")
    parser.add_argument("--ratio-high", type=float, default=0.12, help="Fraction of attempts that are HIGH")
    parser.add_argument("--ratio-edge-low", type=float, default=0.08, help="Fraction of attempts that are LOW edge-case scenarios")
    parser.add_argument("--min-questions", type=int, default=6, help="Minimum questions per attempt")
    parser.add_argument("--max-questions", type=int, default=10, help="Maximum questions per attempt")
    parser.add_argument("--minutes-between", type=float, default=5.0, help="Minutes between attempt start timestamps")
    parser.add_argument("--days-back", type=int, default=7, help="How many days back attempt timestamps start")
    parser.add_argument("--print-samples", type=int, default=0, help="Print N example attempt summaries")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    attempts = int(args.attempts if args.n is None else args.n)

    records = generate_dataset(
        attempts=attempts,
        seed=int(args.seed),
        ratio_low=float(args.ratio_low),
        ratio_medium=float(args.ratio_medium),
        ratio_high=float(args.ratio_high),
        ratio_edge_low=float(args.ratio_edge_low),
        min_questions=int(args.min_questions),
        max_questions=int(args.max_questions),
        minutes_between=float(args.minutes_between),
        days_back=int(args.days_back),
    )

    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")

    low = sum(1 for r in records if r["expected_label"] == "LOW")
    med = sum(1 for r in records if r["expected_label"] == "MEDIUM")
    high = sum(1 for r in records if r["expected_label"] == "HIGH")
    edge = sum(1 for r in records if r.get("is_edge_case"))
    by_profile: Dict[str, int] = {}
    for r in records:
        p = str(r.get("profile") or "")
        by_profile[p] = by_profile.get(p, 0) + 1

    print(f"Wrote {len(records)} attempts → {out_path}")
    print(f"Label distribution: LOW={low} MEDIUM={med} HIGH={high} (edge LOW={edge})")
    print("Profile distribution:")
    for k in sorted(by_profile.keys()):
        print(f"  {k}: {by_profile[k]}")

    if int(args.print_samples) > 0:
        print("\n=== Sample Attempts ===")
        for rec in records[: int(args.print_samples)]:
            print(
                f"- {rec.get('attempt_id')} label={rec.get('expected_label')} profile={rec.get('profile')} "
                f"edge={bool(rec.get('is_edge_case'))} events={len(rec.get('events') or [])}"
            )
            if rec.get("edge_case_scenario"):
                print(f"  scenario={rec.get('edge_case_scenario')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
