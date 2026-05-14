from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from engine.db.models import CaseStatus


class ReviewerActionOut(BaseModel):
    id: int
    case_id: int
    reviewer_id: int
    reviewer_name: Optional[str] = None
    reviewer_email: Optional[str] = None
    action_type: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime


class InvestigationCaseOut(BaseModel):
    id: int
    attempt_id: str
    candidate_id: Optional[str] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    assessment_id: Optional[str] = None
    assessment_name: Optional[str] = None
    status: CaseStatus
    assigned_reviewer_id: Optional[int] = None
    assigned_reviewer_name: Optional[str] = None
    assigned_reviewer_email: Optional[str] = None
    final_decision: Optional[CaseStatus] = None
    resolved_at: Optional[datetime] = None
    escalation_level: int = 0
    current_risk: Optional[str] = None
    current_confidence: Optional[float] = None
    current_combined_score: Optional[float] = None
    latest_event_at: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    action_count: int = 0


class CaseDetailOut(InvestigationCaseOut):
    actions: List[ReviewerActionOut] = Field(default_factory=list)


class CaseListResponse(BaseModel):
    status: str = "success"
    count: int
    data: List[InvestigationCaseOut]


class CaseDetailResponse(BaseModel):
    status: str = "success"
    data: CaseDetailOut


class CaseAssignRequest(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=1000)


class CaseTransitionRequest(BaseModel):
    new_status: CaseStatus
    comment: Optional[str] = Field(default=None, max_length=1000)


class CaseNoteRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)
