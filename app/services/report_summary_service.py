from __future__ import annotations

from typing import Any, Dict, List

from app.services.evidence_service import HIGH, LOW, MEDIUM, normalize_risk_level, safe_float


def _decision_title(risk_level: str) -> str:
    if risk_level == HIGH:
        return "Escalation recommended"
    if risk_level == MEDIUM:
        return "Reviewer assessment required"
    return "Stable session with limited concern"


def _recommendation(risk_level: str) -> str:
    if risk_level == HIGH:
        return "Repeated, connected integrity signals warrant escalation before any final outcome is released."
    if risk_level == MEDIUM:
        return "This attempt contains enough ambiguity to require reviewer judgement before a final decision."
    return "Behavior remains largely stable; continue with normal review unless new evidence appears."


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


def _clean_sentence(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    return text[:-1] if text.endswith(".") else text


def _singularize(title: str) -> str:
    normalized = str(title or "Behavioral signal").strip()
    if normalized.endswith("ies"):
        return f"{normalized[:-3]}y"
    if normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _item_reason_phrase(item: Dict[str, Any] | None) -> str:
    if not item:
        return ""

    reviewer_summary = _clean_sentence(str(item.get("reviewer_summary") or ""))
    if reviewer_summary:
        return reviewer_summary

    explanation = _clean_sentence(str(item.get("explanation") or ""))
    if explanation:
        return explanation

    count = int(item.get("count") or 0)
    title = _singularize(str(item.get("title") or "Behavioral signal"))
    if count > 1:
        return f"{title} was observed repeatedly across the attempt"
    return f"{title} was observed during the attempt"


def _title_fragment(item: Dict[str, Any]) -> str:
    title = _singularize(str(item.get("title") or "signal")).lower()
    count = int(item.get("count") or 0)
    if count > 1:
        return f"repeated {title} events"
    return title


def _strongest_reason(item: Dict[str, Any] | None, risk_level: str) -> str:
    if item:
        phrase = _item_reason_phrase(item)
        if phrase:
            return f"{phrase}."
    if risk_level == HIGH:
        return "Multiple deterministic integrity signals place this attempt in the HIGH risk band."
    if risk_level == MEDIUM:
        return "Repeated but not yet conclusive irregularities place this attempt in the MEDIUM risk band."
    return "Observed behavior remains within a low-concern range."


def _why_this_score(
    *,
    risk_level: str,
    risk_score: float,
    evidence_items: List[Dict[str, Any]],
    session_narrative: str = "",
) -> str:
    if session_narrative:
        return session_narrative
    if evidence_items:
        lead_items = evidence_items[:3]
        parts = [_title_fragment(item) for item in lead_items]
        joined = ", ".join(parts[:-1]) + (f", and {parts[-1]}" if len(parts) > 1 else parts[0])
        if risk_level == HIGH:
            return (
                f"The score settled at {risk_score:.2f} because the attempt shows {joined}, "
                "with those behaviors appearing in connected, reviewer-relevant patterns."
            )
        if risk_level == MEDIUM:
            return (
                f"The score settled at {risk_score:.2f} because the attempt includes {joined}. "
                "The signal mix is meaningful enough to warrant manual review, but it is still context-dependent."
            )
        return (
            f"The score remained at {risk_score:.2f} because only limited signals such as {joined} were observed, "
            "without sustained suspicious progression."
        )
    if risk_level == HIGH:
        return f"The score settled at {risk_score:.2f}, which remains inside the HIGH risk band after deterministic review."
    if risk_level == MEDIUM:
        return f"The score settled at {risk_score:.2f}, which places the attempt in the MEDIUM risk band for reviewer follow-up."
    return f"The score settled at {risk_score:.2f}, which keeps the attempt in the LOW risk band."


def _insight_from_item(item: Dict[str, Any]) -> str:
    title = str(item.get("title") or "Behavioral signal")
    reviewer_summary = _clean_sentence(str(item.get("reviewer_summary") or ""))
    explanation = _clean_sentence(str(item.get("explanation") or ""))
    count = int(item.get("count") or 0)

    if reviewer_summary:
        return f"{reviewer_summary}."
    if explanation:
        return f"{explanation}."
    if count > 1:
        return f"{title} occurred {count} times."
    return f"{title} was observed."


def _build_investigation_insights(*, risk_level: str, evidence_items: List[Dict[str, Any]]) -> List[str]:
    ranked_items = sorted(
        evidence_items,
        key=lambda item: (
            -_severity_rank(str(item.get("severity") or LOW)),
            -int(item.get("count") or 0),
            -int(item.get("related_event_count") or 0),
            str(item.get("title") or ""),
        ),
    )
    insights: List[str] = []
    seen_titles: set[str] = set()

    for item in ranked_items:
        title = str(item.get("title") or "").strip().lower()
        if not title or title in seen_titles or title == "high risk score":
            continue
        seen_titles.add(title)
        insights.append(_insight_from_item(item))
        if len(insights) >= 4:
            break

    if insights:
        return insights
    if risk_level == HIGH:
        return ["Multiple deterministic integrity signals escalated over the course of the attempt."]
    if risk_level == MEDIUM:
        return ["Moderate behavioral irregularities were observed and should be reviewed in context."]
    return ["Assessment engagement remained stable without repeated suspicious progression."]


def build_report_summary(
    *,
    risk_score: Any,
    confidence: Any,
    evidence_items: List[Dict[str, Any]],
    session_narrative: str = "",
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
            session_narrative=session_narrative,
        ),
        "recommendation": _recommendation(risk_level),
        "risk_level": risk_level,
        "risk_score": round(numeric_score, 4),
        "confidence": round(numeric_confidence, 4),
        "investigation_insights": _build_investigation_insights(risk_level=risk_level, evidence_items=evidence_items),
    }
