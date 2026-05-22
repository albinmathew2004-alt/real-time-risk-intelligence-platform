from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.evidence_service import normalize_risk_level, safe_float


def _get(source: Any, field_name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(field_name, default)
    return getattr(source, field_name, default)


def build_final_risk_assessment(
    *,
    attempt_id: str,
    persisted_state: Any = None,
    latest_history: Any = None,
    latest_attempt_log: Any = None,
    latest_event: Any = None,
    current_risk: Optional[Dict[str, Any]] = None,
    fallback_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_state = fallback_result or current_risk or {}

    combined_score = None
    for source, field_name in (
        (persisted_state, "final_risk_score"),
        (latest_history, "combined_score"),
        (runtime_state, "combined_score"),
        (latest_attempt_log, "combined_score"),
    ):
        value = _get(source, field_name)
        if value is not None:
            combined_score = safe_float(value, 0.0)
            break
    if combined_score is None:
        combined_score = 0.0

    confidence = None
    for source, field_name in (
        (persisted_state, "confidence"),
        (latest_history, "confidence"),
        (runtime_state, "confidence"),
        (latest_attempt_log, "confidence"),
        (latest_attempt_log, "confidence_score"),
    ):
        value = _get(source, field_name)
        if value is not None:
            confidence = safe_float(value, 0.0)
            break
    if confidence is None:
        confidence = 0.0

    strongest_reason = (
        str(_get(persisted_state, "strongest_reason") or "").strip()
        or
        str(_get(latest_history, "reason") or "").strip()
        or str(_get(runtime_state, "strongest_reason") or "").strip()
        or str(_get(runtime_state, "explanation") or "").strip()
    )

    generated_at = (
        _get(persisted_state, "updated_at")
        or
        _get(latest_history, "timestamp")
        or _get(runtime_state, "generated_at")
        or _get(runtime_state, "updated_at")
        or _get(latest_event, "received_at")
        or _get(latest_event, "occurred_at")
        or _get(latest_attempt_log, "timestamp")
    )

    candidate_name = (
        _get(persisted_state, "candidate_name")
        or
        _get(latest_history, "candidate_name")
        or _get(runtime_state, "candidate_name")
        or _get(latest_event, "candidate_name")
    )
    candidate_email = (
        _get(persisted_state, "candidate_email")
        or
        _get(latest_history, "candidate_email")
        or _get(runtime_state, "candidate_email")
        or _get(latest_event, "candidate_email")
    )
    candidate_id = (
        _get(persisted_state, "candidate_id")
        or
        _get(latest_history, "candidate_id")
        or _get(runtime_state, "candidate_id")
        or _get(latest_event, "candidate_id")
    )
    assessment_name = (
        _get(persisted_state, "assessment_name")
        or
        _get(latest_history, "assessment_name")
        or _get(runtime_state, "assessment_name")
        or _get(latest_event, "assessment_name")
    )
    assessment_id = (
        _get(persisted_state, "assessment_id")
        or
        _get(latest_history, "assessment_id")
        or _get(runtime_state, "assessment_id")
        or _get(latest_event, "assessment_id")
    )

    raw_risk_level = str(
        _get(persisted_state, "final_risk_level")
        or _get(latest_history, "risk")
        or _get(runtime_state, "risk")
        or ""
    ).strip().upper()
    if raw_risk_level in {"HIGH", "MEDIUM", "LOW"}:
        risk_level = raw_risk_level
    else:
        risk_level = normalize_risk_level(combined_score)

    return {
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "assessment_id": assessment_id,
        "assessment_name": assessment_name,
        "combined_score": round(safe_float(combined_score, 0.0), 4),
        "confidence": round(safe_float(confidence, 0.0), 4),
        "risk_level": risk_level,
        "risk": risk_level,
        "strongest_reason": strongest_reason,
        "generated_at": generated_at,
    }
