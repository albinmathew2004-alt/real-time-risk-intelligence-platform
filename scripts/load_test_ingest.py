import argparse
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import requests


EVENT_TYPES = (
    "exam_started",
    "question_view",
    "question_answer",
    "visibility_change",
    "clipboard",
    "idle_state",
)

_thread_local = threading.local()


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        _thread_local.session = session
    return session


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def build_event(candidate_number: int, event_index: int, started_at: datetime) -> dict:
    attempt_id = f"loadtest_{candidate_number:05d}"
    event_type = EVENT_TYPES[event_index % len(EVENT_TYPES)]
    occurred_at = started_at + timedelta(seconds=event_index * random.randint(2, 9))
    payload = {}

    if event_type == "question_view":
        payload = {"question_id": f"Q{(event_index % 20) + 1}"}
    elif event_type == "question_answer":
        payload = {"question_id": f"Q{(event_index % 20) + 1}", "duration_s": random.randint(8, 45)}
    elif event_type == "visibility_change":
        payload = {"state": "hidden" if event_index % 2 else "visible"}
    elif event_type == "clipboard":
        payload = {"operation": "paste"}
    elif event_type == "idle_state":
        payload = {"duration_s": random.randint(15, 180)}

    return {
        "attempt_id": attempt_id,
        "candidate_id": f"candidate_{candidate_number:05d}",
        "candidate_name": f"Load Test Candidate {candidate_number}",
        "candidate_email": f"candidate{candidate_number}@loadtest.local",
        "assessment_id": "assessment_python_01",
        "assessment_name": "Python Coding Assessment",
        "event_type": event_type,
        "payload": payload,
        "occurred_at": occurred_at.isoformat(),
    }


def post_event(base_url: str, payload: dict, timeout: float) -> tuple[bool, float, str]:
    session = get_session()
    start = time.perf_counter()
    try:
        response = session.post(f"{base_url.rstrip('/')}/v1/events/ingest", json=payload, timeout=timeout)
        latency = time.perf_counter() - start
        if 200 <= response.status_code < 300:
            return True, latency, ""
        return False, latency, f"HTTP {response.status_code}: {response.text[:200]}"
    except Exception as exc:
        latency = time.perf_counter() - start
        return False, latency, str(exc)


def run_scenario(base_url: str, candidates: int, events_per_candidate: int, concurrency: int, timeout: float) -> None:
    total_events = candidates * events_per_candidate
    print(f"\n=== Load test: candidates={candidates}, events_per_candidate={events_per_candidate}, concurrency={concurrency} ===")
    started = time.perf_counter()
    latencies: List[float] = []
    failures: List[str] = []
    success_count = 0

    started_at = datetime.now(timezone.utc)
    payloads = (
        build_event(candidate_number, event_index, started_at + timedelta(seconds=candidate_number))
        for candidate_number in range(1, candidates + 1)
        for event_index in range(events_per_candidate)
    )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(post_event, base_url, payload, timeout) for payload in payloads]
        for future in as_completed(futures):
            ok, latency, error = future.result()
            latencies.append(latency)
            if ok:
                success_count += 1
            elif error:
                failures.append(error)

    elapsed = max(time.perf_counter() - started, 0.001)
    failure_count = total_events - success_count
    avg_latency = statistics.fmean(latencies) if latencies else 0.0
    p95_latency = percentile(latencies, 0.95)

    print(f"Total events:     {total_events}")
    print(f"Success count:    {success_count}")
    print(f"Failure count:    {failure_count}")
    print(f"Events / sec:     {total_events / elapsed:0.2f}")
    print(f"Average latency:  {avg_latency * 1000:0.2f} ms")
    print(f"P95 latency:      {p95_latency * 1000:0.2f} ms")
    if failures:
        print("Sample failure:   " + failures[0])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load test /v1/events/ingest with simulated candidate telemetry.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument(
        "--candidates",
        nargs="+",
        type=int,
        default=[100, 500, 1000],
        help="One or more candidate counts to simulate. Default: 100 500 1000",
    )
    parser.add_argument("--events-per-candidate", type=int, default=12, help="Number of events to send per candidate")
    parser.add_argument("--concurrency", type=int, default=40, help="Concurrent ingestion workers")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for candidate_count in args.candidates:
        run_scenario(
            base_url=args.base_url,
            candidates=candidate_count,
            events_per_candidate=args.events_per_candidate,
            concurrency=args.concurrency,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    main()
