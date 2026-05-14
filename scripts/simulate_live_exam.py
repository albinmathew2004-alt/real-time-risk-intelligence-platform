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
        "attempt_id": "demo_normal_1001",
        "candidate_name": "Nina Verma",
        "candidate_email": "nina.verma@example.com",
        "profile": "NormalThoughtfulCandidate",
    },
    {
        "attempt_id": "demo_borderline_1002",
        "candidate_name": "Rahul Mehta",
        "candidate_email": "rahul.mehta@example.com",
        "profile": "BorderlineCandidate",
    },
    {
        "attempt_id": "demo_cheater_1003",
        "candidate_name": "Kavya Sen",
        "candidate_email": "kavya.sen@example.com",
        "profile": "AggressiveCheater",
    },
    {
        "attempt_id": "demo_template_1004",
        "candidate_name": "Ishaan Roy",
        "candidate_email": "ishaan.roy@example.com",
        "profile": "TemplateCopier",
    },
]


def now_iso(offset_seconds=0.0):
    return (datetime.now(timezone.utc) + timedelta(seconds=float(offset_seconds))).isoformat()


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
        except Exception:
            pass

    except Exception as e:
        print("Error:", e)


def build_event(
    attempt_id,
    candidate_name,
    candidate_email,
    assessment_name,
    event_type,
    payload,
    offset=0,
):
    return {
        "attempt_id": attempt_id,
        "candidate_id": attempt_id.replace("attempt", "candidate"),
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "assessment_id": "assessment_python_01",
        "assessment_name": assessment_name,
        "event_type": event_type,
        "payload": payload,
        "occurred_at": now_iso(offset),
    }


def _default_question_ids():
    # Keep this consistent with your engine expectations (question_view enter/leave pairs).
    n = random.randint(5, 8)
    return [f"q{i+1}" for i in range(n)]


def _to_built_events(
    *,
    attempt_id: str,
    candidate_name: str,
    candidate_email: str,
    assessment_name: str,
    behavior_events: list[BehaviorEvent],
) -> list[dict]:
    built: list[dict] = []

    # Required bookends.
    built.append(
        build_event(
            attempt_id,
            candidate_name,
            candidate_email,
            assessment_name,
            "exam_started",
            {},
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
                assessment_name,
                ev.event_type,
                ev.payload,
                float(ev.offset_s),
            )
        )

    # Add a submit event slightly after the last behavioral action.
    if behavior_events:
        last_offset = max(float(e.offset_s) for e in behavior_events)
    else:
        last_offset = 60.0

    built.append(
        build_event(
            attempt_id,
            candidate_name,
            candidate_email,
            assessment_name,
            "exam_submitted",
            {},
            float(last_offset + random.uniform(8.0, 25.0)),
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
):
    assessment_name = "Python Coding Assessment"

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

    question_ids = _default_question_ids()
    behavior_events = profile.generate(question_ids=question_ids)
    events = _to_built_events(
        attempt_id=attempt_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
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
            attempt_id=f"attempt_{1000 + i}",
            candidate_name=name,
            candidate_email=email,
            candidate_type=chosen,
            base_url=str(args.base_url),
            dry_run=bool(args.dry_run),
            max_events=args.max_events,
            sleep_min_s=float(args.sleep_min),
            sleep_max_s=float(args.sleep_max),
            seed=args.seed,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
