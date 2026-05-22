from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String

from .database import Base


class CaseStatus(str, Enum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    ESCALATED = "ESCALATED"
    CLEARED = "CLEARED"
    CONFIRMED_RISK = "CONFIRMED_RISK"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    CLOSED = "CLOSED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AttemptLog(Base):
    __tablename__ = "attempt_logs"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String, index=True)
    risk = Column(String)
    confidence = Column(Float)
    confidence_score = Column(Float)
    combined_score = Column(Float)
    features = Column(JSON)
    signals = Column(JSON)
    timestamp = Column(String)


class AttemptState(Base):
    __tablename__ = "attempt_states"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String, unique=True, index=True, nullable=False)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    status = Column(String, nullable=False, default="ONGOING", index=True)
    review_status = Column(String, nullable=True, index=True)

    final_risk_level = Column(String, nullable=True, index=True)
    final_risk_score = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    strongest_reason = Column(String, nullable=True)

    violation_overview = Column(JSON, nullable=True)
    evidence_summary = Column(JSON, nullable=True)
    risk_history = Column(JSON, nullable=True)
    features = Column(JSON, nullable=True)
    signals = Column(JSON, nullable=True)

    latest_event_type = Column(String, nullable=True)
    latest_event_at = Column(String, nullable=True)
    event_count = Column(Integer, nullable=False, default=0)

    submitted_at = Column(String, nullable=True)
    updated_at = Column(String, nullable=False)


class RawExamEvent(Base):
    __tablename__ = "raw_exam_events"

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(String, index=True)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    event_type = Column(String, index=True)
    payload = Column(JSON)

    occurred_at = Column(String)
    received_at = Column(String)


class RiskHistory(Base):
    __tablename__ = "risk_history"

    id = Column(Integer, primary_key=True, index=True)

    attempt_id = Column(String, index=True)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    risk = Column(String)
    confidence = Column(Float)
    combined_score = Column(Float)

    reason = Column(String)
    timestamp = Column(String)


class InvestigationCase(Base):
    __tablename__ = "investigation_cases"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(String, unique=True, index=True, nullable=False)

    candidate_id = Column(String, nullable=True, index=True)
    candidate_name = Column(String, nullable=True)
    candidate_email = Column(String, nullable=True)

    assessment_id = Column(String, nullable=True, index=True)
    assessment_name = Column(String, nullable=True)

    status = Column(String, nullable=False, default=CaseStatus.NEW.value, index=True)
    assigned_reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    final_decision = Column(String, nullable=True, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    escalation_level = Column(Integer, nullable=False, default=0)

    current_risk = Column(String, nullable=True, index=True)
    current_confidence = Column(Float, nullable=True)
    current_combined_score = Column(Float, nullable=True)
    latest_event_at = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class ReviewerAction(Base):
    __tablename__ = "reviewer_actions"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("investigation_cases.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False, index=True)
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=True)
    comment = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
