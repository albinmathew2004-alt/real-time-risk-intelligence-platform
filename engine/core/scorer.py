from engine.core.risk_engine import score_event_batch

def run_scoring(events, attempt_id):
    result = score_event_batch(events, attempt_id=attempt_id)

    return {
        "attempt_id": result.attempt_id,
        "risk": result.risk,
        "confidence": result.confidence_score,
        "combined_score": result.combined_score,
        "explanation": result.explanation_text,
        "session_intelligence": result.session_intelligence,
        "timeline_points": result.session_intelligence.get("timeline_points", []),
    }
