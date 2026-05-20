import argparse
import random
import time
from datetime import datetime, timedelta, timezone

import sys
from pathlib import Path

import requests

# Ensure this script can be run as: python scripts/simulate_live_exam.py
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from behavior_profiles import (
    BehaviorEvent,
    all_profiles,
    choose_profile_weighted,
    profile_by_name,
)


BASE_URL = "http://127.0.0.1:8000"


CANDIDATES = [
    ("Aarav Sharma", "aarav@example.com"),
    ("Neha Patel", "neha@example.com"),
    ("Rohan Nair", "rohan@example.com"),
    ("Ananya Iyer", "ananya@example.com"),
    ("Vikram Menon", "vikram@example.com"),
    ("Meera Thomas", "meera@example.com"),
    ("Arjun Rao", "arjun@example.com"),
    ("Priya Das", "priya@example.com"),
    ("Kiran Joseph", "kiran@example.com"),
    ("Sneha Kapoor", "sneha@example.com"),
]

DEMO_PROFILES = [
    {
        "attempt_id": "demo_low_aarav_1001",
        "candidate_name": "Aarav Menon",
        "candidate_email": "aarav.menon@example.com",
        "profile": "DemoLowAaravCandidate",
        "question_count": 8,
    },
    {
        "attempt_id": "demo_medium_neha_1002",
        "candidate_name": "Neha Kapoor",
        "candidate_email": "neha.kapoor@example.com",
        "profile": "DemoMediumNehaCandidate",
        "question_count": 8,
    },
    {
        "attempt_id": "demo_high_rohan_1003",
        "candidate_name": "Rohan Malhotra",
        "candidate_email": "rohan.malhotra@example.com",
        "profile": "DemoHighRohanCandidate",
        "question_count": 9,
    },
]


def timestamp_from_base(base_time: datetime, offset_seconds=0.0):
    return (base_time + timedelta(seconds=float(offset_seconds))).isoformat()


def send_event(event, *, base_url: str):
    try:
        response = requests.post(
            f"{base_url}/v1/events/ingest",
            json=event,
            timeout=10,
        )

        print(
            f"{event['attempt_id']} | "
            f"{event['candidate_name']} | "
            f"{event['event_type']} | "
            f"{response.status_code}"
        )

        try:
            data = response.json()
            print(
                f"  -> Risk: {data.get('current_risk')} | "
                f"Score: {data.get('current_score')}"
            )
            if response.status_code >= 400:
                print(f"  -> Error: {data}")
        except Exception:
            if response.status_code >= 400:
                print(f"  -> Error body: {response.text}")

    except Exception as e:
        print("Error:", e)


def build_event(
    attempt_id,
    candidate_name,
    candidate_email,
    assessment_id,
    assessment_name,
    event_type,
    payload,
    base_time,
    offset=0,
):
    return {
        "attempt_id": attempt_id,
        "candidate_id": attempt_id.replace("attempt", "candidate"),
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "assessment_id": assessment_id,
        "assessment_name": assessment_name,
        "event_type": event_type,
        "payload": payload,
        "occurred_at": timestamp_from_base(base_time, offset),
    }


def _default_question_ids():
    # Keep this consistent with your engine expectations (question_view enter/leave pairs).
    n = random.randint(5, 8)
    return [f"q{i+1}" for i in range(n)]


def _question_ids_for_count(count: int):
    return [f"q{i+1}" for i in range(max(1, int(count)))]


def _to_built_events(
    *,
    attempt_id: str,
    candidate_name: str,
    candidate_email: str,
    assessment_id: str,
    assessment_name: str,
    behavior_events: list[BehaviorEvent],
) -> list[dict]:
    built: list[dict] = []
    max_behavior_offset = max((float(e.offset_s) for e in behavior_events), default=60.0)
    submit_offset = float(max_behavior_offset + random.uniform(8.0, 25.0))
    session_duration_s = max(submit_offset, 60.0)
    session_start = datetime.now(timezone.utc) - timedelta(seconds=session_duration_s + 15.0)

    # Required bookends.
    built.append(
        build_event(
            attempt_id,
            candidate_name,
            candidate_email,
            assessment_id,
            assessment_name,
            "exam_started",
            {},
            session_start,
            0.0,
        )
    )

    # Sort by offset to send in chronological order.
    for ev in sorted(behavior_events, key=lambda e: (float(e.offset_s), str(e.event_type))):
        built.append(
            build_event(
                attempt_id,
                candidate_name,
                candidate_email,
                assessment_id,
                assessment_name,
                ev.event_type,
                ev.payload,
                session_start,
                float(ev.offset_s),
            )
        )

    built.append(
        build_event(
            attempt_id,
            candidate_name,
            candidate_email,
            assessment_id,
            assessment_name,
            "exam_submitted",
            {},
            session_start,
            submit_offset,
        )
    )

    # Ensure stable order
    return sorted(built, key=lambda x: x["occurred_at"])


def simulate_candidate(
    attempt_id,
    candidate_name,
    candidate_email,
    candidate_type="normal",
    *,
    base_url: str = BASE_URL,
    dry_run: bool = False,
    max_events: int | None = None,
    sleep_min_s: float = 1.0,
    sleep_max_s: float = 2.0,
    seed: int | None = None,
    question_count: int | None = None,
    assessment_id: str = "assessment_python_01",
    assessment_name: str = "Python Coding Assessment",
):
    print("\n===================================")
    print(f"Starting simulation for {candidate_name}")
    print(f"Candidate type: {str(candidate_type).upper()}")
    print("===================================\n")

    # Map legacy types to new profiles.
    # This preserves the old 'normal/medium/high' usage while enabling
    # more realistic behavior generation.
    legacy_map = {
        "normal": "NormalThoughtfulCandidate",
        "medium": "BorderlineCandidate",
        "high": "AggressiveCheater",
    }

    ct = str(candidate_type or "").strip()
    if ct in legacy_map:
        profile = profile_by_name(legacy_map[ct])
    else:
        profile = profile_by_name(ct)

    question_ids = _question_ids_for_count(question_count) if question_count else _default_question_ids()
    behavior_events = profile.generate(question_ids=question_ids)
    events = _to_built_events(
        attempt_id=attempt_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        assessment_id=assessment_id,
        assessment_name=assessment_name,
        behavior_events=behavior_events,
    )

    if max_events is not None:
        events = events[: int(max_events)]

    if dry_run:
        print(f"DRY RUN: {profile.name} generated {len(events)} events")
        for ev in events:
            et = ev.get("event_type")
            payload = ev.get("payload") or {}
            if payload:
                print(f"  - {et:<16} {payload}")
            else:
                print(f"  - {et}")
        return

    for event in events:
        send_event(event, base_url=base_url)
        time.sleep(random.uniform(float(sleep_min_s), float(sleep_max_s)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate realistic assessment behavior and stream events to /v1/events/ingest"
    )
    parser.add_argument("--base-url", default=BASE_URL, help="FastAPI base URL")
    parser.add_argument("--attempt-id", default=None, help="Override the attempt_id for a single-candidate simulation")
    parser.add_argument("--candidate-name", default=None, help="Override the candidate_name for a single-candidate simulation")
    parser.add_argument("--candidate-email", default=None, help="Override the candidate_email for a single-candidate simulation")
    parser.add_argument("--assessment-id", default="assessment_python_01", help="Assessment identifier to send with events")
    parser.add_argument("--assessment-name", default="Python Coding Assessment", help="Assessment name to send with events")
    parser.add_argument(
        "--profile",
        default="weighted",
        help=(
            "Profile name (e.g., BorderlineCandidate). Use 'weighted' to choose a realistic mix. "
            "Legacy aliases: normal, medium, high."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print events instead of sending")
    parser.add_argument(
        "--demo-pack",
        action="store_true",
        help="Send a predictable four-candidate enterprise demo pack with the required profiles",
    )
    parser.add_argument("--max-events", type=int, default=None, help="Limit printed/sent events")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--sleep-min", type=float, default=1.0, help="Min sleep between events")
    parser.add_argument("--sleep-max", type=float, default=2.0, help="Max sleep between events")
    parser.add_argument(
        "--n-candidates",
        type=int,
        default=len(CANDIDATES),
        help="How many candidates from the built-in list to simulate",
    )
    args = parser.parse_args()

    base_seed = int(args.seed) if args.seed is not None else None

    print("Available profiles:")
    print("  - " + "\n  - ".join(p.name for p in all_profiles()))

    if args.demo_pack:
        print("\nRunning predictable enterprise demo pack:")
        for index, candidate in enumerate(DEMO_PROFILES, start=1):
            if base_seed is not None:
                random.seed(base_seed + index)
            simulate_candidate(
                attempt_id=candidate["attempt_id"],
                candidate_name=candidate["candidate_name"],
                candidate_email=candidate["candidate_email"],
                candidate_type=candidate["profile"],
                base_url=str(args.base_url),
                dry_run=bool(args.dry_run),
                max_events=args.max_events,
                sleep_min_s=float(args.sleep_min),
                sleep_max_s=float(args.sleep_max),
                seed=args.seed,
                question_count=int(candidate.get("question_count") or 0) or None,
                assessment_id=str(args.assessment_id),
                assessment_name=str(args.assessment_name),
            )
        return 0

    n_candidates = max(1, min(int(args.n_candidates), len(CANDIDATES)))

    for i, (name, email) in enumerate(CANDIDATES[:n_candidates], start=1):
        if base_seed is not None:
            random.seed(base_seed + i)
        if str(args.profile).strip().lower() == "weighted":
            chosen = choose_profile_weighted().name
        else:
            chosen = str(args.profile)

        simulate_candidate(
            attempt_id=str(args.attempt_id or f"attempt_{1000 + i}"),
            candidate_name=str(args.candidate_name or name),
            candidate_email=str(args.candidate_email or email),
            candidate_type=chosen,
            base_url=str(args.base_url),
            dry_run=bool(args.dry_run),
            max_events=args.max_events,
            sleep_min_s=float(args.sleep_min),
            sleep_max_s=float(args.sleep_max),
            seed=args.seed,
            assessment_id=str(args.assessment_id),
            assessment_name=str(args.assessment_name),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
