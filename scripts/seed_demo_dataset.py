from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.main import (  # noqa: E402
    ATTEMPT_LOG_FILE,
    _persist_timeline_snapshots,
    _record_risk_snapshot_if_needed,
    _build_dashboard_summary,
    build_reason,
)
from app.models.user import User, UserRole  # noqa: E402
from app.services.final_assessment_service import build_final_risk_assessment  # noqa: E402
from engine.core import risk_engine  # noqa: E402
from engine.core.event_processor import normalize_events  # noqa: E402
from engine.core.feature_engineering import build_features  # noqa: E402
from engine.core.risk_engine import score_event_batch  # noqa: E402
from engine.core.types import ScoringConfig  # noqa: E402
from engine.db.database import Base, SessionLocal, engine, ensure_demo_schema  # noqa: E402
from engine.db.models import AttemptLog, CaseStatus, InvestigationCase, RawExamEvent, ReviewerAction  # noqa: E402
from engine.db.models import RiskHistory  # noqa: E402
from simulate_live_exam import build_event  # noqa: E402
from behavior_profiles import profile_by_name  # noqa: E402


DEMO_DATASET_PREFIX = "demo_dataset"
DEFAULT_COUNT = 100
DEFAULT_SEED = 42
PERSIST_DEMO_DATA = os.getenv("PERSIST_DEMO_DATA", "true").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_TARGET_LOW = 0.66
DEFAULT_TARGET_MEDIUM = 0.22
DEFAULT_TARGET_HIGH = 0.12
ASSESSMENTS = [
    ("assessment_python_01", "Python Coding Assessment"),
    ("assessment_sql_01", "SQL Analytics Assessment"),
    ("assessment_java_01", "Java Backend Assessment"),
    ("assessment_js_01", "JavaScript Frontend Assessment"),
    ("assessment_data_01", "Data Reasoning Assessment"),
]
LOW_RANGE = (0.05, 0.30)
MEDIUM_RANGE = (0.40, 0.68)
HIGH_RANGE = (0.80, 0.95)
LOW_PROFILES = [
    "NormalThoughtfulCandidate",
    "FastButLegitCandidate",
    "DistractedButLegitCandidate",
    "BurnoutCandidate",
    "OverpreparedFastCandidate",
    "DemoLowAaravCandidate",
]
MEDIUM_PROFILES = [
    "BorderlineCandidate",
    "PanicSwitcherCandidate",
    "SearcherPatternCandidate",
    "DemoMediumNehaCandidate",
]
HIGH_PROFILES = [
    "DemoHighRohanCandidate",
    "HighRiskLongSessionCandidate",
    "TemplateCopier",
    "AggressiveCheater",
]
OPEN_CASE_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.TRIAGED.value,
    CaseStatus.UNDER_INVESTIGATION.value,
    CaseStatus.ESCALATED.value,
}
SCORING_CFG = ScoringConfig()

INDIAN_FIRST_NAMES = [
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya", "Meera", "Neha",
    "Nikhil", "Priya", "Rohan", "Saanvi", "Siddharth", "Tanvi", "Varun", "Zoya", "Riya", "Dev",
    "Rahul", "Sneha", "Manav", "Pooja", "Vikram", "Shreya", "Aditya", "Ira", "Kabir", "Naina",
]
INTERNATIONAL_FIRST_NAMES = [
    "Amelia", "Daniel", "Elena", "Gabriel", "Hannah", "Isabella", "Jonah", "Lena", "Marcus", "Maya",
    "Noah", "Olivia", "Sofia", "Theo", "Yuki", "Zara", "Luca", "Mila", "Owen", "Chloe",
    "Nathan", "Leah", "Julian", "Ava", "Mateo", "Nora", "Ethan", "Sasha", "Leo", "Emily",
]
LAST_NAMES = [
    "Menon", "Kapoor", "Malhotra", "Rao", "Iyer", "Patel", "Sharma", "Nair", "Thomas", "Das",
    "Bose", "Sen", "Khan", "Singh", "Gupta", "Fernandes", "D'Souza", "Chopra", "Verma", "Joshi",
    "Müller", "Garcia", "Chen", "Smith", "Lopez", "Kim", "Johnson", "Martinez", "Brown", "Lee",
]
EMAIL_DOMAINS = [
    "example.com",
    "maildemo.io",
    "talenthub.ai",
    "candidate.net",
    "proctoriq.demo",
]


@dataclass
class CandidateSeedRecord:
    attempt_id: str
    candidate_name: str
    candidate_email: str
    assessment_id: str
    assessment_name: str
    risk_bucket: str
    profile_name: str
    question_count: int
    events: list[dict[str, Any]]
    features: dict[str, Any]
    result: Any
    session_start: datetime
    session_end: datetime
    include_submit: bool
    case_status: str
    assigned_reviewer_id: int | None
    assigned_reviewer_name: str | None
    action_count: int


def _slugify(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _question_count_for_bucket(bucket: str, rng: random.Random) -> int:
    if bucket == "LOW":
        return rng.randint(7, 10)
    if bucket == "MEDIUM":
        return rng.randint(8, 11)
    return rng.randint(8, 12)


def _distribution_for_count(total: int, *, target_low: float, target_medium: float, target_high: float) -> dict[str, int]:
    weights = [max(0.0, target_low), max(0.0, target_medium), max(0.0, target_high)]
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [DEFAULT_TARGET_LOW, DEFAULT_TARGET_MEDIUM, DEFAULT_TARGET_HIGH]
        total_weight = sum(weights)
    normalized = [weight / total_weight for weight in weights]
    low = round(total * normalized[0])
    medium = round(total * normalized[1])
    high = total - low - medium
    return {"LOW": low, "MEDIUM": medium, "HIGH": high}


def _iter_candidate_identities(count: int) -> list[tuple[str, str]]:
    first_names = INDIAN_FIRST_NAMES + INTERNATIONAL_FIRST_NAMES
    identities: list[tuple[str, str]] = []
    used_emails: set[str] = set()
    for idx in range(count):
        first = first_names[idx % len(first_names)]
        last = LAST_NAMES[(idx // len(first_names)) % len(LAST_NAMES)]
        name = f"{first} {last}"
        domain = EMAIL_DOMAINS[idx % len(EMAIL_DOMAINS)]
        email_base = f"{_slugify(first)}.{_slugify(last)}"
        suffix = 1
        email = f"{email_base}@{domain}"
        while email in used_emails:
            suffix += 1
            email = f"{email_base}{suffix}@{domain}"
        used_emails.add(email)
        identities.append((name, email))
    return identities


def _session_end_for_index(index: int, total: int, rng: random.Random, *, historical: bool) -> datetime:
    now = datetime.now(timezone.utc)
    if not historical:
        minimum_end = now - timedelta(hours=4)
        latest_end = now - timedelta(minutes=5)
        span_seconds = max(60, int((latest_end - minimum_end).total_seconds()))
        offset_seconds = rng.randint(0, span_seconds)
        candidate = minimum_end + timedelta(seconds=offset_seconds)
        jitter = timedelta(seconds=(index % 11) * 17 + rng.randint(0, 29))
        end_time = min(candidate + jitter, latest_end)
        return end_time.astimezone(timezone.utc)

    band = index / max(1, total - 1)
    if band < 0.58:
        days_back = 0
    elif band < 0.82:
        days_back = 1
    else:
        days_back = rng.randint(2, 6)
    hour = rng.randint(8, 20)
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    base_day = (now - timedelta(days=days_back)).date()
    end_time = datetime(base_day.year, base_day.month, base_day.day, hour, minute, second, tzinfo=timezone.utc)
    if end_time > now - timedelta(minutes=5):
        end_time = now - timedelta(minutes=rng.randint(5, 120))
    return end_time


def _ongoing_session_end(rng: random.Random) -> datetime:
    now = datetime.now(timezone.utc)
    return now - timedelta(minutes=rng.randint(3, 35), seconds=rng.randint(0, 40))


def _assessment_for_index(index: int) -> tuple[str, str]:
    return ASSESSMENTS[index % len(ASSESSMENTS)]


def _build_events_for_profile(
    *,
    attempt_id: str,
    candidate_name: str,
    candidate_email: str,
    assessment_id: str,
    assessment_name: str,
    profile_name: str,
    question_count: int,
    session_end: datetime,
    include_submit: bool,
) -> tuple[list[dict[str, Any]], datetime]:
    profile = profile_by_name(profile_name)
    question_ids = [f"q{i+1}" for i in range(max(1, int(question_count)))]
    behavior_events = profile.generate(question_ids=question_ids)
    max_behavior_offset = max((float(event.offset_s) for event in behavior_events), default=60.0)
    submit_offset = float(max_behavior_offset + 18.0)
    session_start = session_end - timedelta(seconds=submit_offset)

    events: list[dict[str, Any]] = [
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
    ]
    for event in sorted(behavior_events, key=lambda item: (float(item.offset_s), str(item.event_type))):
        events.append(
            build_event(
                attempt_id,
                candidate_name,
                candidate_email,
                assessment_id,
                assessment_name,
                event.event_type,
                event.payload,
                session_start,
                float(event.offset_s),
            )
        )
    if include_submit:
        events.append(
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
    events.sort(key=lambda item: item["occurred_at"])
    return events, session_start


def _score_distance(score: float, low: float, high: float) -> float:
    if low <= score <= high:
        return 0.0
    if score < low:
        return low - score
    return score - high


def _target_profiles(bucket: str) -> tuple[list[str], tuple[float, float]]:
    if bucket == "LOW":
        return LOW_PROFILES, LOW_RANGE
    if bucket == "MEDIUM":
        return MEDIUM_PROFILES, MEDIUM_RANGE
    return HIGH_PROFILES, HIGH_RANGE


def _match_bucket(bucket: str, risk_label: str, score: float) -> bool:
    low, high = _target_profiles(bucket)[1]
    return risk_label == bucket and low <= score <= high


def _select_case_status(bucket: str, rng: random.Random) -> str:
    if bucket == "LOW":
        return rng.choices(
            [CaseStatus.CLEARED.value, CaseStatus.FALSE_POSITIVE.value, CaseStatus.CLOSED.value, CaseStatus.NEW.value, CaseStatus.TRIAGED.value],
            weights=[24, 16, 18, 6, 4],
            k=1,
        )[0]
    if bucket == "MEDIUM":
        return rng.choices(
            [
                CaseStatus.NEW.value,
                CaseStatus.TRIAGED.value,
                CaseStatus.UNDER_INVESTIGATION.value,
                CaseStatus.ESCALATED.value,
                CaseStatus.FALSE_POSITIVE.value,
                CaseStatus.CLEARED.value,
                CaseStatus.CLOSED.value,
            ],
            weights=[5, 11, 13, 6, 4, 3, 2],
            k=1,
        )[0]
    return rng.choices(
        [
            CaseStatus.NEW.value,
            CaseStatus.ESCALATED.value,
            CaseStatus.UNDER_INVESTIGATION.value,
            CaseStatus.CONFIRMED_RISK.value,
            CaseStatus.CLOSED.value,
        ],
        weights=[3, 14, 10, 5, 2],
        k=1,
    )[0]


def _should_leave_session_open(bucket: str, bucket_position: int) -> bool:
    if bucket == "HIGH":
        return bucket_position <= 1
    if bucket == "MEDIUM":
        return bucket_position <= 2
    return bucket_position <= 3


def _latest_event_by_attempt(db) -> dict[str, RawExamEvent]:
    rows = (
        db.query(RawExamEvent)
        .filter(RawExamEvent.attempt_id.like(f"{DEMO_DATASET_PREFIX}%"))
        .order_by(RawExamEvent.received_at.desc(), RawExamEvent.id.desc())
        .all()
    )
    latest: dict[str, RawExamEvent] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in latest:
            latest[row.attempt_id] = row
    return latest


def _latest_history_by_attempt(db) -> dict[str, RiskHistory]:
    rows = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id.like(f"{DEMO_DATASET_PREFIX}%"))
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .all()
    )
    latest: dict[str, RiskHistory] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in latest:
            latest[row.attempt_id] = row
    return latest


def _latest_attempt_log_by_attempt(db) -> dict[str, AttemptLog]:
    rows = (
        db.query(AttemptLog)
        .filter(AttemptLog.attempt_id.like(f"{DEMO_DATASET_PREFIX}%"))
        .order_by(AttemptLog.id.desc())
        .all()
    )
    latest: dict[str, AttemptLog] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in latest:
            latest[row.attempt_id] = row
    return latest


def _actual_distribution(db) -> dict[str, Any]:
    cases = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.attempt_id.like(f"{DEMO_DATASET_PREFIX}%"))
        .order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc())
        .all()
    )
    latest_events = _latest_event_by_attempt(db)
    latest_history = _latest_history_by_attempt(db)
    latest_logs = _latest_attempt_log_by_attempt(db)

    risk_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    high_samples: list[dict[str, Any]] = []
    medium_samples: list[dict[str, Any]] = []

    for case in cases:
        attempt_id = str(case.attempt_id or "")
        assessment = build_final_risk_assessment(
            attempt_id=attempt_id,
            latest_history=latest_history.get(attempt_id),
            latest_attempt_log=latest_logs.get(attempt_id),
            latest_event=latest_events.get(attempt_id),
            current_risk=None,
            fallback_result={
                "candidate_id": case.candidate_id,
                "candidate_name": case.candidate_name,
                "candidate_email": case.candidate_email,
                "assessment_id": case.assessment_id,
                "assessment_name": case.assessment_name,
                "risk": case.current_risk,
                "confidence": case.current_confidence,
                "combined_score": case.current_combined_score,
                "generated_at": case.latest_event_at,
            },
        )
        risk_level = str(assessment.get("risk_level") or case.current_risk or "LOW")
        risk_counts[risk_level] += 1
        status = str(case.status or CaseStatus.NEW.value)
        status_counts[status] += 1

        sample_row = {
            "attempt_id": attempt_id,
            "candidate_name": assessment.get("candidate_name") or case.candidate_name,
            "score": round(float(assessment.get("combined_score") or 0.0), 4),
            "status": status,
            "assessment_name": assessment.get("assessment_name") or case.assessment_name,
        }
        if risk_level == "HIGH" and len(high_samples) < 5:
            high_samples.append(sample_row)
        if risk_level == "MEDIUM" and len(medium_samples) < 5:
            medium_samples.append(sample_row)

    open_count = sum(status_counts.get(status, 0) for status in OPEN_CASE_STATUSES)
    resolved_count = sum(
        count for status, count in status_counts.items() if status not in OPEN_CASE_STATUSES
    )
    return {
        "risk_counts": {
            "LOW": risk_counts.get("LOW", 0),
            "MEDIUM": risk_counts.get("MEDIUM", 0),
            "HIGH": risk_counts.get("HIGH", 0),
        },
        "status_counts": dict(status_counts),
        "open_count": open_count,
        "resolved_count": resolved_count,
        "high_samples": high_samples,
        "medium_samples": medium_samples,
    }


def _distribution_warnings(*, actual_counts: dict[str, int], total_count: int, target_distribution: dict[str, int]) -> list[str]:
    warnings: list[str] = []
    for bucket in ("LOW", "MEDIUM", "HIGH"):
        target = target_distribution.get(bucket, 0)
        actual = actual_counts.get(bucket, 0)
        tolerance = max(2, round(total_count * 0.04))
        if abs(actual - target) > tolerance:
            warnings.append(
                f"{bucket} distribution is outside target tolerance: expected about {target}, got {actual}."
            )
    return warnings


def _dataset_attempt_id(bucket: str, ordinal: int) -> str:
    return f"{DEMO_DATASET_PREFIX}_{bucket.lower()}_{ordinal:04d}"


def _build_manual_log_entry(record: CandidateSeedRecord) -> dict[str, Any]:
    result = record.result
    return {
        "timestamp": record.session_end.isoformat(),
        "attempt_id": record.attempt_id,
        "candidate_name": record.candidate_name,
        "candidate_email": record.candidate_email,
        "assessment_name": record.assessment_name,
        "risk": result.risk,
        "confidence": float(result.confidence),
        "confidence_score": float(result.confidence_score),
        "combined_score": float(result.combined_score),
        "features": record.features,
        "signals": result.signals,
        "session_intelligence": getattr(result, "session_intelligence", {}),
        "dataset_seed": True,
    }


def _existing_attempt_ids(db) -> set[str]:
    ids = {
        value
        for (value,) in db.query(RawExamEvent.attempt_id).distinct().all()
        if value and str(value).startswith(DEMO_DATASET_PREFIX)
    }
    return ids


def _delete_existing_dataset(db) -> None:
    dataset_cases = db.query(InvestigationCase).filter(InvestigationCase.attempt_id.like(f"{DEMO_DATASET_PREFIX}%")).all()
    dataset_case_ids = [case.id for case in dataset_cases]
    if dataset_case_ids:
        db.query(ReviewerAction).filter(ReviewerAction.case_id.in_(dataset_case_ids)).delete(synchronize_session=False)
    db.query(InvestigationCase).filter(InvestigationCase.attempt_id.like(f"{DEMO_DATASET_PREFIX}%")).delete(synchronize_session=False)
    db.query(RiskHistory).filter(RiskHistory.attempt_id.like(f"{DEMO_DATASET_PREFIX}%")).delete(synchronize_session=False)
    db.query(AttemptLog).filter(AttemptLog.attempt_id.like(f"{DEMO_DATASET_PREFIX}%")).delete(synchronize_session=False)
    db.query(RawExamEvent).filter(RawExamEvent.attempt_id.like(f"{DEMO_DATASET_PREFIX}%")).delete(synchronize_session=False)
    db.commit()

    if ATTEMPT_LOG_FILE.exists():
        kept_lines: list[str] = []
        with ATTEMPT_LOG_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue
                if str(payload.get("attempt_id") or "").startswith(DEMO_DATASET_PREFIX):
                    continue
                kept_lines.append(line)
        ATTEMPT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ATTEMPT_LOG_FILE.write_text("".join(kept_lines), encoding="utf-8")


def _seed_candidate_record(
    db,
    *,
    record: CandidateSeedRecord,
    reviewer_pool: list[User],
) -> None:
    result = record.result
    features = record.features
    for event in record.events:
        db.add(
            RawExamEvent(
                attempt_id=record.attempt_id,
                candidate_id=record.attempt_id,
                candidate_name=record.candidate_name,
                candidate_email=record.candidate_email,
                assessment_id=record.assessment_id,
                assessment_name=record.assessment_name,
                event_type=event["event_type"],
                payload=event["payload"],
                occurred_at=event["occurred_at"],
                received_at=event["occurred_at"],
            )
        )

    db.add(
        AttemptLog(
            attempt_id=record.attempt_id,
            risk=result.risk,
            confidence=float(result.confidence),
            confidence_score=float(result.confidence_score),
            combined_score=float(result.combined_score),
            features=features,
            signals=result.signals,
            timestamp=record.session_end.isoformat(),
        )
    )

    timeline_points = list((getattr(result, "session_intelligence", {}) or {}).get("timeline_points", []))
    _persist_timeline_snapshots(
        db,
        attempt_id=record.attempt_id,
        candidate_id=record.attempt_id,
        candidate_name=record.candidate_name,
        candidate_email=record.candidate_email,
        assessment_id=record.assessment_id,
        assessment_name=record.assessment_name,
        timeline_points=timeline_points,
        final_confidence=float(result.confidence_score),
    )
    _record_risk_snapshot_if_needed(
        db,
        attempt_id=record.attempt_id,
        candidate_id=record.attempt_id,
        candidate_name=record.candidate_name,
        candidate_email=record.candidate_email,
        assessment_id=record.assessment_id,
        assessment_name=record.assessment_name,
        risk_level=str(result.risk),
        confidence=float(result.confidence_score),
        score=float(result.combined_score),
        reason=build_reason(
            {
                "risk": result.risk,
                "explanation": result.explanation_text,
                "session_intelligence": getattr(result, "session_intelligence", {}),
            }
        ),
        timestamp=record.session_end.isoformat(),
        force=True,
    )

    case_created_at = record.session_start
    case_updated_at = record.session_end
    final_decision = None
    resolved_at = None
    escalation_level = 0
    if record.case_status == CaseStatus.ESCALATED.value:
        escalation_level = 1
    if record.case_status in {CaseStatus.CLEARED.value, CaseStatus.FALSE_POSITIVE.value, CaseStatus.CONFIRMED_RISK.value}:
        final_decision = record.case_status
        resolved_at = record.session_end
    if record.case_status == CaseStatus.CLOSED.value:
        final_decision = CaseStatus.CONFIRMED_RISK.value if record.risk_bucket == "HIGH" else CaseStatus.CLEARED.value
        resolved_at = record.session_end

    case = InvestigationCase(
        attempt_id=record.attempt_id,
        candidate_id=record.attempt_id,
        candidate_name=record.candidate_name,
        candidate_email=record.candidate_email,
        assessment_id=record.assessment_id,
        assessment_name=record.assessment_name,
        status=record.case_status,
        assigned_reviewer_id=record.assigned_reviewer_id,
        final_decision=final_decision,
        resolved_at=resolved_at,
        escalation_level=escalation_level,
        current_risk=str(result.risk),
        current_confidence=float(result.confidence_score),
        current_combined_score=float(result.combined_score),
        latest_event_at=record.session_end.isoformat(),
        created_at=case_created_at,
        updated_at=case_updated_at,
    )
    db.add(case)
    db.flush()

    action_timestamp = record.session_start + timedelta(minutes=2)
    if record.assigned_reviewer_id is not None:
        db.add(
            ReviewerAction(
                case_id=case.id,
                reviewer_id=record.assigned_reviewer_id,
                action_type="ASSIGN",
                previous_status=CaseStatus.NEW.value,
                new_status=record.case_status if record.case_status in {CaseStatus.TRIAGED.value, CaseStatus.UNDER_INVESTIGATION.value, CaseStatus.ESCALATED.value} else CaseStatus.TRIAGED.value,
                comment=f"Assigned during demo dataset seeding to {record.assigned_reviewer_name or 'reviewer'}.",
                created_at=action_timestamp,
            )
        )
    if record.case_status in {CaseStatus.CLEARED.value, CaseStatus.FALSE_POSITIVE.value, CaseStatus.CONFIRMED_RISK.value, CaseStatus.CLOSED.value} and reviewer_pool:
        reviewer = reviewer_pool[(case.id + len(record.attempt_id)) % len(reviewer_pool)]
        db.add(
            ReviewerAction(
                case_id=case.id,
                reviewer_id=reviewer.id,
                action_type="TRANSITION",
                previous_status=CaseStatus.UNDER_INVESTIGATION.value,
                new_status=record.case_status,
                comment=f"Demo review completed with outcome {record.case_status.replace('_', ' ').title()}.",
                created_at=record.session_end - timedelta(minutes=1),
            )
        )


def _append_attempt_logs(records: Iterable[CandidateSeedRecord]) -> None:
    ATTEMPT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ATTEMPT_LOG_FILE.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_build_manual_log_entry(record)) + "\n")


def _generate_candidate_record(
    *,
    bucket: str,
    ordinal: int,
    total: int,
    bucket_position: int,
    identity: tuple[str, str],
    reviewer_pool: list[User],
    rng: random.Random,
    historical: bool,
) -> CandidateSeedRecord:
    name, email = identity
    attempt_id = _dataset_attempt_id(bucket, ordinal)
    assessment_id, assessment_name = _assessment_for_index(ordinal - 1)
    profiles, score_range = _target_profiles(bucket)
    include_submit = not _should_leave_session_open(bucket, bucket_position)
    best_match: tuple[float, str, int, list[dict[str, Any]], Any, datetime, datetime] | None = None

    for trial in range(18):
        profile_name = profiles[trial % len(profiles)]
        question_count = _question_count_for_bucket(bucket, rng)
        if include_submit:
            session_end = _session_end_for_index(ordinal - 1 + trial, total, rng, historical=historical)
        else:
            session_end = _ongoing_session_end(rng)
        events, session_start = _build_events_for_profile(
            attempt_id=attempt_id,
            candidate_name=name,
            candidate_email=email,
            assessment_id=assessment_id,
            assessment_name=assessment_name,
            profile_name=profile_name,
            question_count=question_count,
            session_end=session_end,
            include_submit=include_submit,
        )
        processed = normalize_events(events, attempt_id=attempt_id)
        features = build_features(processed.events, SCORING_CFG.feature)
        result = score_event_batch(events, attempt_id=attempt_id)
        score_value = float(result.combined_score)
        distance = _score_distance(score_value, score_range[0], score_range[1])
        target_midpoint = (score_range[0] + score_range[1]) / 2.0
        distance += abs(score_value - target_midpoint) * 0.35
        if str(result.risk) != bucket:
            distance += 1.0
        if bucket == "MEDIUM":
            distance += abs(float(features.get("paste_count", 0.0)) - rng.uniform(3.0, 5.0)) * 0.03
            distance += abs(float(features.get("tab_hidden_count", 0.0)) - rng.uniform(5.0, 8.0)) * 0.025
        if bucket == "HIGH":
            distance += abs(float(features.get("paste_count", 0.0)) - rng.uniform(6.0, 8.0)) * 0.035
            distance += abs(float(features.get("tab_hidden_count", 0.0)) - rng.uniform(10.0, 13.0)) * 0.03
        candidate_value = (distance, profile_name, question_count, events, features, result, session_start, session_end)
        if best_match is None or candidate_value[0] < best_match[0]:
            best_match = candidate_value
        if _match_bucket(bucket, str(result.risk), score_value):
            best_match = candidate_value
            break

    assert best_match is not None
    _, profile_name, question_count, events, features, result, session_start, session_end = best_match
    assigned_reviewer_id = None
    assigned_reviewer_name = None
    case_status = _select_case_status(bucket, rng) if include_submit else rng.choice(
        [CaseStatus.NEW.value, CaseStatus.TRIAGED.value, CaseStatus.UNDER_INVESTIGATION.value]
    )
    if reviewer_pool and case_status in {
        CaseStatus.TRIAGED.value,
        CaseStatus.UNDER_INVESTIGATION.value,
        CaseStatus.ESCALATED.value,
        CaseStatus.CLEARED.value,
        CaseStatus.FALSE_POSITIVE.value,
        CaseStatus.CONFIRMED_RISK.value,
        CaseStatus.CLOSED.value,
    }:
        reviewer = reviewer_pool[ordinal % len(reviewer_pool)]
        assigned_reviewer_id = reviewer.id
        assigned_reviewer_name = reviewer.full_name or reviewer.email

    return CandidateSeedRecord(
        attempt_id=attempt_id,
        candidate_name=name,
        candidate_email=email,
        assessment_id=assessment_id,
        assessment_name=assessment_name,
        risk_bucket=bucket,
        profile_name=profile_name,
        question_count=question_count,
        events=events,
        features=features,
        result=result,
        session_start=session_start,
        session_end=session_end,
        include_submit=include_submit,
        case_status=case_status,
        assigned_reviewer_id=assigned_reviewer_id,
        assigned_reviewer_name=assigned_reviewer_name,
        action_count=2 if assigned_reviewer_id is not None else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a persistent enterprise-style demo dataset into the local backend database.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Approximate number of demo candidates to generate")
    parser.add_argument("--reset", action="store_true", help="Explicitly delete and rebuild only the persistent demo dataset attempts")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Deterministic random seed")
    parser.add_argument("--historical", action="store_true", help="Spread seeded attempts across today, yesterday, and the last 7 days")
    parser.add_argument("--target-low", type=float, default=DEFAULT_TARGET_LOW, help="Target LOW distribution weight or ratio")
    parser.add_argument("--target-medium", type=float, default=DEFAULT_TARGET_MEDIUM, help="Target MEDIUM distribution weight or ratio")
    parser.add_argument("--target-high", type=float, default=DEFAULT_TARGET_HIGH, help="Target HIGH distribution weight or ratio")
    args = parser.parse_args()

    count = max(12, int(args.count))
    rng = random.Random(int(args.seed))

    Base.metadata.create_all(bind=engine)
    ensure_demo_schema()

    original_log_attempt = risk_engine.log_attempt
    risk_engine.log_attempt = lambda *_, **__: None

    db = SessionLocal()
    try:
        if args.reset:
            _delete_existing_dataset(db)
        existing_attempt_ids = _existing_attempt_ids(db)
        reviewer_pool = (
            db.query(User)
            .filter(User.role.in_([UserRole.ADMIN.value, UserRole.REVIEWER.value]), User.is_active.is_(True))
            .order_by(User.id.asc())
            .all()
        )

        target_distribution = _distribution_for_count(
            count,
            target_low=float(args.target_low),
            target_medium=float(args.target_medium),
            target_high=float(args.target_high),
        )
        identities = _iter_candidate_identities(count)
        records_to_seed: list[CandidateSeedRecord] = []
        ordinal = 0
        for bucket in ("LOW", "MEDIUM", "HIGH"):
            for bucket_position in range(1, target_distribution[bucket] + 1):
                ordinal += 1
                attempt_id = _dataset_attempt_id(bucket, ordinal)
                if PERSIST_DEMO_DATA and attempt_id in existing_attempt_ids:
                    continue
                records_to_seed.append(
                    _generate_candidate_record(
                        bucket=bucket,
                        ordinal=ordinal,
                        total=count,
                        bucket_position=bucket_position,
                        identity=identities[ordinal - 1],
                        reviewer_pool=reviewer_pool,
                        rng=rng,
                        historical=bool(args.historical),
                    )
                )

        for record in records_to_seed:
            _seed_candidate_record(db, record=record, reviewer_pool=reviewer_pool)
        db.commit()
        _append_attempt_logs(records_to_seed)

        seeded_distribution = Counter(record.risk_bucket for record in records_to_seed)
        actual_distribution = _actual_distribution(db)
        dashboard_summary = _build_dashboard_summary(db, recent_hours=24)
        distribution_warnings = _distribution_warnings(
            actual_counts=actual_distribution["risk_counts"],
            total_count=sum(actual_distribution["risk_counts"].values()),
            target_distribution=target_distribution,
        )
        print("Persistent demo dataset seeding complete.")
        print(
            json.dumps(
                {
                    "persist_demo_data": PERSIST_DEMO_DATA,
                    "requested_count": count,
                    "historical_mode": bool(args.historical),
                    "seeded_count": len(records_to_seed),
                    "skipped_existing": max(0, count - len(records_to_seed)),
                    "target_distribution": target_distribution,
                    "requested_bucket_distribution": {
                        "LOW": seeded_distribution.get("LOW", 0),
                        "MEDIUM": seeded_distribution.get("MEDIUM", 0),
                        "HIGH": seeded_distribution.get("HIGH", 0),
                    },
                    "actual_scored_distribution": actual_distribution["risk_counts"],
                    "actual_status_distribution": {
                        "OPEN": actual_distribution["open_count"],
                        "NEW": actual_distribution["status_counts"].get(CaseStatus.NEW.value, 0),
                        "UNDER_INVESTIGATION": actual_distribution["status_counts"].get(CaseStatus.UNDER_INVESTIGATION.value, 0),
                        "RESOLVED": sum(
                            actual_distribution["status_counts"].get(status, 0)
                            for status in (
                                CaseStatus.CLEARED.value,
                                CaseStatus.FALSE_POSITIVE.value,
                                CaseStatus.CONFIRMED_RISK.value,
                            )
                        ),
                        "CLOSED": actual_distribution["status_counts"].get(CaseStatus.CLOSED.value, 0),
                        "CONFIRMED_RISK": actual_distribution["status_counts"].get(CaseStatus.CONFIRMED_RISK.value, 0),
                        "resolved_total": actual_distribution["resolved_count"],
                    },
                    "dashboard_visibility": {
                        "high_risk_now": dashboard_summary.get("high_risk_count"),
                        "medium_risk": dashboard_summary.get("medium_risk_count"),
                        "needs_review": dashboard_summary.get("needs_review_count"),
                    },
                    "warnings": distribution_warnings,
                    "sample_high_candidates": actual_distribution["high_samples"],
                    "sample_medium_candidates": actual_distribution["medium_samples"],
                    "sample_candidates": [
                        {
                            "attempt_id": record.attempt_id,
                            "candidate_name": record.candidate_name,
                            "assessment_name": record.assessment_name,
                            "risk": record.result.risk,
                            "score": round(float(record.result.combined_score), 4),
                            "status": record.case_status,
                            "profile": record.profile_name,
                            "session_end": record.session_end.isoformat(),
                        }
                        for record in records_to_seed[:8]
                    ],
                },
                indent=2,
            )
        )
    finally:
        risk_engine.log_attempt = original_log_attempt
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
