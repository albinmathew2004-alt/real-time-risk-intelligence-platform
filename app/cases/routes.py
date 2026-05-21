from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct

from app.auth.dependencies import require_reviewer
from app.models.user import User
from app.services.evidence_service import normalize_evidence, normalize_risk_level, safe_float
from app.services.final_assessment_service import build_final_risk_assessment
from app.schemas.cases import (
    CaseAssignRequest,
    CaseDetailOut,
    CaseDetailResponse,
    CaseListResponse,
    CaseNoteRequest,
    CaseTransitionRequest,
    InvestigationCaseOut,
    ReviewerActionOut,
)
from engine.cache.redis_client import get_current_risk
from engine.db.database import SessionLocal
from engine.db.models import (
    AttemptLog,
    CaseStatus,
    InvestigationCase,
    RawExamEvent,
    ReviewerAction,
    RiskHistory,
)


router = APIRouter(prefix="/v1/cases", tags=["cases"])
ACTIONABLE_CASE_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.TRIAGED.value,
    CaseStatus.UNDER_INVESTIGATION.value,
    CaseStatus.ESCALATED.value,
}
COMPLETED_CASE_STATUSES = {
    "RESOLVED",
    CaseStatus.CLEARED.value,
    CaseStatus.CONFIRMED_RISK.value,
    CaseStatus.FALSE_POSITIVE.value,
    CaseStatus.CLOSED.value,
    "COMPLETED",
}

TERMINAL_DECISIONS = {
    CaseStatus.CLEARED.value,
    CaseStatus.CONFIRMED_RISK.value,
    CaseStatus.FALSE_POSITIVE.value,
}

ALLOWED_TRANSITIONS = {
    CaseStatus.NEW.value: {
        CaseStatus.TRIAGED.value,
        CaseStatus.UNDER_INVESTIGATION.value,
        CaseStatus.ESCALATED.value,
        CaseStatus.CLEARED.value,
        CaseStatus.CONFIRMED_RISK.value,
        CaseStatus.FALSE_POSITIVE.value,
    },
    CaseStatus.TRIAGED.value: {
        CaseStatus.UNDER_INVESTIGATION.value,
        CaseStatus.ESCALATED.value,
        CaseStatus.CLEARED.value,
        CaseStatus.CONFIRMED_RISK.value,
        CaseStatus.FALSE_POSITIVE.value,
    },
    CaseStatus.UNDER_INVESTIGATION.value: {
        CaseStatus.ESCALATED.value,
        CaseStatus.CLEARED.value,
        CaseStatus.CONFIRMED_RISK.value,
        CaseStatus.FALSE_POSITIVE.value,
    },
    CaseStatus.ESCALATED.value: {
        CaseStatus.UNDER_INVESTIGATION.value,
        CaseStatus.CLEARED.value,
        CaseStatus.CONFIRMED_RISK.value,
        CaseStatus.FALSE_POSITIVE.value,
    },
    CaseStatus.CLEARED.value: {CaseStatus.CLOSED.value},
    CaseStatus.CONFIRMED_RISK.value: {CaseStatus.CLOSED.value},
    CaseStatus.FALSE_POSITIVE.value: {CaseStatus.CLOSED.value},
    CaseStatus.CLOSED.value: set(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _recent_cutoff(recent_hours: Optional[int]) -> Optional[datetime]:
    if recent_hours is None or recent_hours <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=recent_hours)


def _is_recent(value: Optional[str], cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return True
    parsed = _parse_iso(value)
    if parsed is None:
        return False
    return parsed >= cutoff


def _latest_snapshot(db, attempt_id: str) -> Optional[Dict[str, object]]:
    current_risk = get_current_risk(attempt_id) or {}
    latest_history = (
        db.query(RiskHistory)
        .filter(RiskHistory.attempt_id == attempt_id)
        .order_by(RiskHistory.timestamp.desc())
        .first()
    )
    latest_event = (
        db.query(RawExamEvent)
        .filter(RawExamEvent.attempt_id == attempt_id)
        .order_by(RawExamEvent.received_at.desc())
        .first()
    )
    latest_attempt_log = (
        db.query(AttemptLog)
        .filter(AttemptLog.attempt_id == attempt_id)
        .order_by(AttemptLog.id.desc())
        .first()
    )

    if not current_risk and not latest_history and not latest_event and not latest_attempt_log:
        return None

    assessment = build_final_risk_assessment(
        attempt_id=attempt_id,
        latest_history=latest_history,
        latest_attempt_log=latest_attempt_log,
        latest_event=latest_event,
        current_risk=current_risk,
    )

    return {
        "candidate_id": assessment.get("candidate_id"),
        "candidate_name": assessment.get("candidate_name"),
        "candidate_email": assessment.get("candidate_email"),
        "assessment_id": assessment.get("assessment_id"),
        "assessment_name": assessment.get("assessment_name"),
        "current_risk": assessment.get("risk_level"),
        "current_confidence": assessment.get("confidence"),
        "current_combined_score": assessment.get("combined_score"),
        "latest_event_at": assessment.get("generated_at"),
    }


def _ensure_case_for_attempt(db, attempt_id: str) -> Optional[InvestigationCase]:
    snapshot = _latest_snapshot(db, attempt_id)
    if snapshot is None:
        return None

    case = db.query(InvestigationCase).filter(InvestigationCase.attempt_id == attempt_id).first()
    if case is None:
        now = _utcnow()
        case = InvestigationCase(
            attempt_id=attempt_id,
            candidate_id=snapshot["candidate_id"],
            candidate_name=snapshot["candidate_name"],
            candidate_email=snapshot["candidate_email"],
            assessment_id=snapshot["assessment_id"],
            assessment_name=snapshot["assessment_name"],
            status=CaseStatus.NEW.value,
            current_risk=snapshot["current_risk"],
            current_confidence=snapshot["current_confidence"],
            current_combined_score=snapshot["current_combined_score"],
            latest_event_at=snapshot["latest_event_at"],
            created_at=now,
            updated_at=now,
        )
        db.add(case)
        db.flush()
        return case

    changed = False
    for field_name in (
        "candidate_id",
        "candidate_name",
        "candidate_email",
        "assessment_id",
        "assessment_name",
        "current_risk",
        "current_confidence",
        "current_combined_score",
        "latest_event_at",
    ):
        value = snapshot[field_name]
        if value is not None and getattr(case, field_name) != value:
            setattr(case, field_name, value)
            changed = True
    if changed:
        case.updated_at = _utcnow()
    return case


def _sync_cases_from_attempts(db) -> None:
    attempt_ids = set()
    attempt_ids.update(
        row[0]
        for row in db.query(distinct(RawExamEvent.attempt_id)).all()
        if row and row[0]
    )
    attempt_ids.update(
        row[0]
        for row in db.query(distinct(RiskHistory.attempt_id)).all()
        if row and row[0]
    )
    attempt_ids.update(
        row[0]
        for row in db.query(distinct(AttemptLog.attempt_id)).all()
        if row and row[0]
    )

    for attempt_id in sorted(attempt_ids):
        _ensure_case_for_attempt(db, attempt_id)

    db.commit()


def _serialize_action(db, action: ReviewerAction) -> ReviewerActionOut:
    reviewer = db.query(User).filter(User.id == action.reviewer_id).first()
    return ReviewerActionOut(
        id=action.id,
        case_id=action.case_id,
        reviewer_id=action.reviewer_id,
        reviewer_name=getattr(reviewer, "full_name", None),
        reviewer_email=getattr(reviewer, "email", None),
        action_type=action.action_type,
        previous_status=action.previous_status,
        new_status=action.new_status,
        comment=action.comment,
        created_at=action.created_at,
    )


def _serialize_case(db, case: InvestigationCase, *, include_actions: bool = False) -> CaseDetailOut | InvestigationCaseOut:
    assigned_user = None
    if case.assigned_reviewer_id:
        assigned_user = db.query(User).filter(User.id == case.assigned_reviewer_id).first()

    actions_query = (
        db.query(ReviewerAction)
        .filter(ReviewerAction.case_id == case.id)
        .order_by(ReviewerAction.created_at.desc(), ReviewerAction.id.desc())
    )
    action_count = actions_query.count()
    actions = [_serialize_action(db, action) for action in actions_query.all()] if include_actions else []

    payload = {
        "id": case.id,
        "attempt_id": case.attempt_id,
        "candidate_id": case.candidate_id,
        "candidate_name": case.candidate_name,
        "candidate_email": case.candidate_email,
        "assessment_id": case.assessment_id,
        "assessment_name": case.assessment_name,
        "status": case.status,
        "assigned_reviewer_id": case.assigned_reviewer_id,
        "assigned_reviewer_name": getattr(assigned_user, "full_name", None),
        "assigned_reviewer_email": getattr(assigned_user, "email", None),
        "final_decision": case.final_decision,
        "resolved_at": case.resolved_at,
        "escalation_level": case.escalation_level or 0,
        "current_risk": case.current_risk,
        "current_confidence": case.current_confidence,
        "current_combined_score": case.current_combined_score,
        "latest_event_at": case.latest_event_at,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "action_count": action_count,
    }

    if include_actions:
        return CaseDetailOut(**payload, actions=actions)
    return InvestigationCaseOut(**payload)


def _queue_status(case: InvestigationCase) -> str:
    status = case.status or CaseStatus.NEW.value
    if status in {CaseStatus.CLEARED.value, CaseStatus.FALSE_POSITIVE.value}:
        return "RESOLVED"
    return status


def _status_priority(status_value: str) -> int:
    if status_value == CaseStatus.ESCALATED.value:
        return 0
    if status_value == CaseStatus.UNDER_INVESTIGATION.value:
        return 1
    if status_value == CaseStatus.TRIAGED.value:
        return 2
    if status_value == CaseStatus.NEW.value:
        return 3
    if status_value in COMPLETED_CASE_STATUSES:
        return 4
    return 7


def _risk_priority(risk_level: str) -> int:
    if risk_level == "HIGH":
        return 0
    if risk_level == "MEDIUM":
        return 1
    return 2


def _event_counts_by_attempt(db) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    rows = db.query(RawExamEvent).all()
    for row in rows:
        if not row.attempt_id:
            continue
        counts[row.attempt_id] = counts.get(row.attempt_id, 0) + 1
    return counts


def _latest_events_by_attempt(db) -> Dict[str, RawExamEvent]:
    rows = (
        db.query(RawExamEvent)
        .order_by(RawExamEvent.received_at.desc(), RawExamEvent.id.desc())
        .all()
    )
    result: Dict[str, RawExamEvent] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in result:
            result[row.attempt_id] = row
    return result


def _latest_attempt_logs_by_attempt(db) -> Dict[str, AttemptLog]:
    rows = (
        db.query(AttemptLog)
        .order_by(AttemptLog.id.desc())
        .all()
    )
    result: Dict[str, AttemptLog] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in result:
            result[row.attempt_id] = row
    return result


def _latest_history_by_attempt(db) -> Dict[str, RiskHistory]:
    rows = (
        db.query(RiskHistory)
        .order_by(RiskHistory.timestamp.desc(), RiskHistory.id.desc())
        .all()
    )
    result: Dict[str, RiskHistory] = {}
    for row in rows:
        if row.attempt_id and row.attempt_id not in result:
            result[row.attempt_id] = row
    return result


def _strongest_signal_for_case(
    *,
    latest_log: Optional[AttemptLog],
    latest_event: Optional[RawExamEvent],
    risk_score: float,
    risk_level: str,
    strongest_reason: str = "",
) -> str:
    if strongest_reason:
        return strongest_reason
    features = getattr(latest_log, "features", None) or {}
    events = []
    if latest_event is not None:
        events.append(
            {
                "event_type": latest_event.event_type,
                "payload": latest_event.payload or {},
                "occurred_at": latest_event.occurred_at,
            }
        )
    evidence_items = normalize_evidence(
        events=events,
        features=features,
        risk_score=risk_score,
        risk_level=risk_level,
    )
    if evidence_items:
        return str(evidence_items[0].get("title") or "Behavioral signal")
    if risk_level == "HIGH":
        return "High Risk Score"
    if risk_level == "MEDIUM":
        return "Elevated Risk Score"
    return "No major signal"


def _build_review_queue_payload(
    db,
    *,
    status_filter: Optional[str],
    risk_level_filter: Optional[str],
    assigned_to_filter: Optional[str],
    search: Optional[str],
    recent_hours: Optional[int] = None,
) -> Dict[str, object]:
    _sync_cases_from_attempts(db)
    cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
    users = {user.id: user for user in db.query(User).all()}
    event_counts = _event_counts_by_attempt(db)
    latest_events = _latest_events_by_attempt(db)
    latest_logs = _latest_attempt_logs_by_attempt(db)
    latest_history = _latest_history_by_attempt(db)

    rows = []
    cutoff = _recent_cutoff(recent_hours)
    for case in cases:
        assigned_user = users.get(case.assigned_reviewer_id) if case.assigned_reviewer_id else None
        risk_score = safe_float(case.current_combined_score, 0.0)
        risk_level = normalize_risk_level(risk_score)
        queue_status = _queue_status(case)
        latest_event = latest_events.get(case.attempt_id)
        latest_log = latest_logs.get(case.attempt_id)
        assigned_to = getattr(assigned_user, "full_name", None) or getattr(assigned_user, "email", None) or "Unassigned"
        last_activity = case.latest_event_at or getattr(latest_event, "received_at", None) or getattr(latest_event, "occurred_at", None)
        canonical_assessment = build_final_risk_assessment(
            attempt_id=case.attempt_id,
            latest_event=latest_event,
            latest_attempt_log=latest_log,
            latest_history=latest_history.get(case.attempt_id),
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
        risk_score = safe_float(canonical_assessment.get("combined_score"), 0.0)
        risk_level = str(canonical_assessment.get("risk_level") or normalize_risk_level(risk_score))

        rows.append(
            {
                "case_id": case.id,
                "attempt_id": case.attempt_id,
                "candidate_name": canonical_assessment.get("candidate_name") or "Unknown Candidate",
                "candidate_email": canonical_assessment.get("candidate_email") or "No email available",
                "assessment_name": canonical_assessment.get("assessment_name") or "Python Coding Assessment",
                "risk_level": risk_level,
                "risk_score": round(risk_score, 4),
                "confidence": round(safe_float(canonical_assessment.get("confidence"), 0.0), 4),
                "case_status": queue_status,
                "assigned_to": assigned_to,
                "last_activity": last_activity,
                "event_count": event_counts.get(case.attempt_id, 0),
                "strongest_signal": _strongest_signal_for_case(
                    latest_log=latest_log,
                    latest_event=latest_event,
                    risk_score=risk_score,
                    risk_level=risk_level,
                    strongest_reason=str(canonical_assessment.get("strongest_reason") or "").strip(),
                ),
            }
        )

    if cutoff is not None:
        recent_rows = [
            row for row in rows
            if _is_recent(row.get("last_activity"), cutoff)
        ]
        if recent_rows:
            rows = recent_rows

    rows.sort(
        key=lambda row: (
            _status_priority(str(row["case_status"])),
            _risk_priority(str(row["risk_level"])),
            -safe_float(row["risk_score"], 0.0),
            -((_parse_iso(row["last_activity"]).timestamp()) if _parse_iso(row["last_activity"]) else 0),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["priority_rank"] = index

    status_counts = {
        "all": len(rows),
        "new": sum(1 for row in rows if row["case_status"] == CaseStatus.NEW.value),
        "under_investigation": sum(1 for row in rows if row["case_status"] == CaseStatus.UNDER_INVESTIGATION.value),
        "escalated": sum(1 for row in rows if row["case_status"] == CaseStatus.ESCALATED.value),
        "resolved": sum(1 for row in rows if row["case_status"] in COMPLETED_CASE_STATUSES),
        "closed": sum(1 for row in rows if row["case_status"] == CaseStatus.CLOSED.value),
    }

    filtered_rows = rows
    if status_filter:
        normalized_status = status_filter.strip().upper()
        status_alias = {
            "UNDER INVESTIGATION": CaseStatus.UNDER_INVESTIGATION.value,
            "UNDER_INVESTIGATION": CaseStatus.UNDER_INVESTIGATION.value,
        }
        normalized_status = status_alias.get(normalized_status, normalized_status)
        if normalized_status != "ALL":
            filtered_rows = [row for row in filtered_rows if row["case_status"] == normalized_status]
    if risk_level_filter:
        normalized_risk = risk_level_filter.strip().upper()
        filtered_rows = [row for row in filtered_rows if row["risk_level"] == normalized_risk]
    if assigned_to_filter:
        assigned_query = assigned_to_filter.strip().lower()
        if assigned_query == "unassigned":
            filtered_rows = [row for row in filtered_rows if row["assigned_to"] == "Unassigned"]
        else:
            filtered_rows = [row for row in filtered_rows if assigned_query in str(row["assigned_to"]).lower()]
    if search:
        needle = search.strip().lower()
        filtered_rows = [
            row for row in filtered_rows
            if needle in " ".join(
                [
                    str(row["attempt_id"]),
                    str(row["candidate_name"]),
                    str(row["candidate_email"]),
                    str(row["assessment_name"]),
                    str(row["strongest_signal"]),
                ]
            ).lower()
        ]

    return {
        "count": len(filtered_rows),
        "status_counts": status_counts,
        "data": filtered_rows,
    }


def _get_case_or_404(db, case_id: int) -> InvestigationCase:
    case = db.query(InvestigationCase).filter(InvestigationCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


def _assert_case_mutable(case: InvestigationCase) -> None:
    if case.status == CaseStatus.CLOSED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closed cases are immutable until reopen support is implemented",
        )


def _validate_transition(case: InvestigationCase, new_status: CaseStatus) -> None:
    target = new_status.value
    if case.status == target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Case is already in status {target}")

    allowed = ALLOWED_TRANSITIONS.get(case.status, set())
    if target not in allowed:
        allowed_list = ", ".join(sorted(allowed)) if allowed else "none"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transition from {case.status} to {target}. Allowed transitions: {allowed_list}",
        )


def _record_action(
    db,
    *,
    case: InvestigationCase,
    reviewer_id: int,
    action_type: str,
    previous_status: Optional[str],
    new_status: Optional[str],
    comment: Optional[str],
) -> None:
    db.add(
        ReviewerAction(
            case_id=case.id,
            reviewer_id=reviewer_id,
            action_type=action_type,
            previous_status=previous_status,
            new_status=new_status,
            comment=comment,
            created_at=_utcnow(),
        )
    )


@router.get("", response_model=CaseListResponse)
def list_cases(_user: User = Depends(require_reviewer)):
    db = SessionLocal()
    try:
        _sync_cases_from_attempts(db)
        cases = db.query(InvestigationCase).order_by(InvestigationCase.updated_at.desc(), InvestigationCase.id.desc()).all()
        data = [_serialize_case(db, case) for case in cases]
        return CaseListResponse(count=len(data), data=data)
    finally:
        db.close()


@router.get("/review-queue")
def review_queue(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    risk_level: Optional[str] = Query(default=None),
    assigned_to: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    recent_hours: Optional[int] = Query(default=24),
    _user: User = Depends(require_reviewer),
):
    db = SessionLocal()
    try:
        return _build_review_queue_payload(
            db,
            status_filter=status_filter,
            risk_level_filter=risk_level,
            assigned_to_filter=assigned_to,
            search=search,
            recent_hours=recent_hours,
        )
    finally:
        db.close()


@router.get("/{case_id}", response_model=CaseDetailResponse)
def get_case(case_id: int, _user: User = Depends(require_reviewer)):
    db = SessionLocal()
    try:
        case = _get_case_or_404(db, case_id)
        case = _ensure_case_for_attempt(db, case.attempt_id) or case
        db.commit()
        return CaseDetailResponse(data=_serialize_case(db, case, include_actions=True))
    finally:
        db.close()


@router.post("/{case_id}/assign", response_model=CaseDetailResponse)
def assign_case(case_id: int, payload: CaseAssignRequest, current_user: User = Depends(require_reviewer)):
    db = SessionLocal()
    try:
        case = _get_case_or_404(db, case_id)
        _assert_case_mutable(case)

        previous_status = case.status
        now = _utcnow()
        if case.status == CaseStatus.NEW.value:
            case.status = CaseStatus.TRIAGED.value
        case.assigned_reviewer_id = current_user.id
        case.updated_at = now

        comment = (payload.comment or f"Assigned to {current_user.email}").strip()
        _record_action(
            db,
            case=case,
            reviewer_id=current_user.id,
            action_type="ASSIGN",
            previous_status=previous_status,
            new_status=case.status,
            comment=comment,
        )
        db.commit()
        db.refresh(case)
        return CaseDetailResponse(data=_serialize_case(db, case, include_actions=True))
    finally:
        db.close()


@router.post("/{case_id}/transition", response_model=CaseDetailResponse)
def transition_case(case_id: int, payload: CaseTransitionRequest, current_user: User = Depends(require_reviewer)):
    db = SessionLocal()
    try:
        case = _get_case_or_404(db, case_id)
        _assert_case_mutable(case)
        _validate_transition(case, payload.new_status)

        previous_status = case.status
        next_status = payload.new_status.value
        now = _utcnow()

        case.status = next_status
        case.updated_at = now

        if next_status == CaseStatus.ESCALATED.value:
            case.escalation_level = int(case.escalation_level or 0) + 1
            case.final_decision = None
            case.resolved_at = None
        elif next_status in TERMINAL_DECISIONS:
            case.final_decision = next_status
            case.resolved_at = now
        elif next_status == CaseStatus.CLOSED.value:
            if case.final_decision is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A final reviewer decision is required before closing a case",
                )
            case.resolved_at = case.resolved_at or now
        else:
            case.resolved_at = None

        _record_action(
            db,
            case=case,
            reviewer_id=current_user.id,
            action_type="TRANSITION",
            previous_status=previous_status,
            new_status=case.status,
            comment=(payload.comment or "").strip() or None,
        )
        db.commit()
        db.refresh(case)
        return CaseDetailResponse(data=_serialize_case(db, case, include_actions=True))
    finally:
        db.close()


@router.post("/{case_id}/notes", response_model=CaseDetailResponse)
def add_case_note(case_id: int, payload: CaseNoteRequest, current_user: User = Depends(require_reviewer)):
    db = SessionLocal()
    try:
        case = _get_case_or_404(db, case_id)
        _assert_case_mutable(case)

        comment = payload.comment.strip()
        if not comment:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment is required")

        previous_status = case.status
        if case.status in {CaseStatus.NEW.value, CaseStatus.TRIAGED.value}:
            case.status = CaseStatus.UNDER_INVESTIGATION.value
        case.updated_at = _utcnow()

        _record_action(
            db,
            case=case,
            reviewer_id=current_user.id,
            action_type="NOTE",
            previous_status=previous_status,
            new_status=case.status,
            comment=comment,
        )
        db.commit()
        db.refresh(case)
        return CaseDetailResponse(data=_serialize_case(db, case, include_actions=True))
    finally:
        db.close()
