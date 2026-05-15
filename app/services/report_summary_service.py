from __future__ import annotations

from typing import Any, Dict, List

from app.services.evidence_service import HIGH, LOW, MEDIUM, normalize_risk_level, safe_float


def _decision_title(risk_level: str) -> str:
    if risk_level == HIGH:
        return "Urgent review required"
    if risk_level == MEDIUM:
        return "Manual review recommended"
    return "No immediate review required"


def _recommendation(risk_level: str) -> str:
    if risk_level == HIGH:
        return "Escalate this attempt for reviewer action before a final decision."
    if risk_level == MEDIUM:
        return "Manually review this attempt before a final decision."
    return "Continue with standard review unless additional evidence appears."


def _severity_rank(value: str) -> int:
    if value == HIGH:
        return 3
    if value == MEDIUM:
        return 2
    return 1


def _strongest_item(evidence_items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not evidence_items:
        return None
    return sorted(
        evidence_items,
        key=lambda item: (
            -_severity_rank(str(item.get("severity") or LOW)),
            -int(item.get("count") or 0),
            str(item.get("title") or ""),
        ),
    )[0]


def _strongest_reason(item: Dict[str, Any] | None, risk_level: str) -> str:
    if item:
        count = int(item.get("count") or 0)
        title = str(item.get("title") or "Behavioral signal")
        if count > 1:
            return f"{title} was observed {count} times."
        return f"{title} was observed."
    if risk_level == HIGH:
        return "The combined score alone places this attempt in the HIGH risk band."
    if risk_level == MEDIUM:
        return "The combined score places this attempt in the MEDIUM risk band."
    return "No significant suspicious signal was detected."


def _why_this_score(
    *,
    risk_level: str,
    risk_score: float,
    evidence_items: List[Dict[str, Any]],
) -> str:
    if evidence_items:
        lead_items = evidence_items[:3]
        parts = []
        for item in lead_items:
            title = str(item.get("title") or "Signal")
            count = int(item.get("count") or 0)
            if count > 1:
                parts.append(f"{title} ({count})")
            else:
                parts.append(title)
        joined = ", ".join(parts[:-1]) + (f", and {parts[-1]}" if len(parts) > 1 else parts[0])
        return (
            f"The normalized report score is {risk_score:.2f} ({risk_level}) because the attempt contains "
            f"{joined}. These signals occurred often enough to justify this risk label."
        )
    if risk_level == HIGH:
        return f"The normalized report score is {risk_score:.2f}, which falls in the HIGH risk band."
    if risk_level == MEDIUM:
        return f"The normalized report score is {risk_score:.2f}, which falls in the MEDIUM risk band."
    return f"The normalized report score is {risk_score:.2f}, which remains in the LOW risk band."


def build_report_summary(
    *,
    risk_score: Any,
    confidence: Any,
    evidence_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    numeric_score = safe_float(risk_score)
    numeric_confidence = safe_float(confidence)
    risk_level = normalize_risk_level(numeric_score)
    strongest_item = _strongest_item(evidence_items)

    return {
        "final_decision_title": _decision_title(risk_level),
        "strongest_reason": _strongest_reason(strongest_item, risk_level),
        "why_this_score": _why_this_score(
            risk_level=risk_level,
            risk_score=numeric_score,
            evidence_items=evidence_items,
        ),
        "recommendation": _recommendation(risk_level),
        "risk_level": risk_level,
        "risk_score": round(numeric_score, 4),
        "confidence": round(numeric_confidence, 4),
    }
