from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct

from app.auth.dependencies import require_reviewer
from app.models.user import User
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

    return {
        "candidate_id": current_risk.get("candidate_id") or getattr(latest_history, "candidate_id", None) or getattr(latest_event, "candidate_id", None),
        "candidate_name": current_risk.get("candidate_name") or getattr(latest_history, "candidate_name", None) or getattr(latest_event, "candidate_name", None),
        "candidate_email": current_risk.get("candidate_email") or getattr(latest_history, "candidate_email", None) or getattr(latest_event, "candidate_email", None),
        "assessment_id": current_risk.get("assessment_id") or getattr(latest_history, "assessment_id", None) or getattr(latest_event, "assessment_id", None),
        "assessment_name": current_risk.get("assessment_name") or getattr(latest_history, "assessment_name", None) or getattr(latest_event, "assessment_name", None),
        "current_risk": current_risk.get("risk") or getattr(latest_history, "risk", None) or getattr(latest_attempt_log, "risk", None),
        "current_confidence": (
            current_risk.get("confidence")
            if current_risk.get("confidence") is not None
            else getattr(latest_history, "confidence", None)
            if latest_history is not None
            else getattr(latest_attempt_log, "confidence", None)
        ),
        "current_combined_score": (
            current_risk.get("combined_score")
            if current_risk.get("combined_score") is not None
            else getattr(latest_history, "combined_score", None)
            if latest_history is not None
            else getattr(latest_attempt_log, "combined_score", None)
        ),
        "latest_event_at": current_risk.get("updated_at")
        or getattr(latest_history, "timestamp", None)
        or getattr(latest_event, "received_at", None)
        or getattr(latest_event, "occurred_at", None)
        or getattr(latest_attempt_log, "timestamp", None),
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
