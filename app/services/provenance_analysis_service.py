from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol
from urllib.parse import urlparse


CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "provenance_reference_corpus.json"
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
PROVENANCE_VALIDATION_TRACK = "Testing Post-Submission Answer Provenance Analysis"
LIMITATIONS_NOTE = "Source matches indicate potential reference overlap, not definitive proof of copying."
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceCandidate:
    source_title: str
    source_url: str | None
    source_domain: str | None
    source_type: str
    retrieved_from: str
    content_snippet: str
    normalized_text: str
    tags: list[str] = field(default_factory=list)
    created_at: str | None = None


@dataclass(slots=True)
class MatchEvidence:
    similarity_score: float
    token_overlap: float
    phrase_overlap: float
    chunk_similarity: float
    matched_candidate_excerpt: str
    matched_reference_excerpt: str
    confidence_label: str
    match_reason: str


@dataclass(slots=True)
class ProvenanceResult:
    likelihood: str
    confidence: float
    top_matches: list[dict[str, Any]]
    behavioral_correlation: list[str]
    reviewer_summary: str
    limitations_note: str


class WebSearchProvider(Protocol):
    def search(self, query: str, *, limit: int = 5) -> list[SourceCandidate]:
        ...


class CrawlerProvider(Protocol):
    def crawl(self, url: str) -> list[SourceCandidate]:
        ...


class OCRProvider(Protocol):
    def extract(self, image_bytes: bytes) -> str:
        ...


class VectorSearchProvider(Protocol):
    def query_similar(self, text: str, *, limit: int = 5) -> list[SourceCandidate]:
        ...


class InactiveProvenanceProviders:
    """TODO: wire real retrieval providers when web-scale provenance search is enabled."""

    web_search_provider: WebSearchProvider | None = None
    crawler_provider: CrawlerProvider | None = None
    OCR_provider: OCRProvider | None = None
    vector_search_provider: VectorSearchProvider | None = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def _parse_iso_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9\s#_./:=()-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clip_text(value: Any, *, max_length: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rstrip()}…"


def _source_domain(url: Any) -> str | None:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    try:
        return urlparse(raw_url).netloc or None
    except ValueError:
        return None


def _tokenize(value: Any) -> list[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return []
    return [token for token in normalized.split(" ") if token]


def _token_overlap_score(candidate_text: str, reference_text: str) -> float:
    candidate_tokens = set(_tokenize(candidate_text))
    reference_tokens = set(_tokenize(reference_text))
    if not candidate_tokens or not reference_tokens:
        return 0.0
    intersection = len(candidate_tokens & reference_tokens)
    union = len(candidate_tokens | reference_tokens)
    jaccard = intersection / max(1, union)
    reference_coverage = intersection / max(1, len(reference_tokens))
    candidate_coverage = intersection / max(1, len(candidate_tokens))
    return max(jaccard, reference_coverage, candidate_coverage * 0.82)


def _phrase_overlap_score(candidate_text: str, reference_text: str) -> float:
    candidate_tokens = _tokenize(candidate_text)
    reference_tokens = _tokenize(reference_text)
    if len(candidate_tokens) < 2 or len(reference_tokens) < 2:
        return 0.0
    candidate_pairs = {" ".join(candidate_tokens[index:index + 2]) for index in range(len(candidate_tokens) - 1)}
    reference_pairs = {" ".join(reference_tokens[index:index + 2]) for index in range(len(reference_tokens) - 1)}
    candidate_triples = {" ".join(candidate_tokens[index:index + 3]) for index in range(len(candidate_tokens) - 2)} if len(candidate_tokens) >= 3 else set()
    reference_triples = {" ".join(reference_tokens[index:index + 3]) for index in range(len(reference_tokens) - 2)} if len(reference_tokens) >= 3 else set()
    scores = []
    if candidate_pairs and reference_pairs:
        scores.append(len(candidate_pairs & reference_pairs) / max(1, min(len(candidate_pairs), len(reference_pairs))))
    if candidate_triples and reference_triples:
        scores.append(len(candidate_triples & reference_triples) / max(1, min(len(candidate_triples), len(reference_triples))))
    if not scores:
        return 0.0
    return max(scores)


def _best_chunk_similarity(candidate_text: str, reference_text: str) -> float:
    candidate_chunks = _chunk_text(candidate_text, chunk_size=220)[:10]
    reference_chunks = _chunk_text(reference_text, chunk_size=220)[:10]
    best_score = 0.0
    for candidate_chunk in candidate_chunks or [candidate_text]:
        for reference_chunk in reference_chunks or [reference_text]:
            sequence_ratio = SequenceMatcher(None, _normalize_text(candidate_chunk), _normalize_text(reference_chunk)).ratio()
            token_ratio = _token_overlap_score(candidate_chunk, reference_chunk)
            phrase_ratio = _phrase_overlap_score(candidate_chunk, reference_chunk)
            score = round((sequence_ratio * 0.4) + (token_ratio * 0.35) + (phrase_ratio * 0.25), 4)
            if score > best_score:
                best_score = score
    return best_score


def _best_chunk_similarity_details(candidate_text: str, reference_text: str) -> tuple[float, str, str]:
    candidate_chunks = _chunk_text(candidate_text, chunk_size=220)[:10] or [_clip_text(candidate_text, max_length=220)]
    reference_chunks = _chunk_text(reference_text, chunk_size=220)[:10] or [_clip_text(reference_text, max_length=220)]
    best_score = 0.0
    best_candidate = candidate_chunks[0]
    best_reference = reference_chunks[0]
    for candidate_chunk in candidate_chunks:
        for reference_chunk in reference_chunks:
            sequence_ratio = SequenceMatcher(None, _normalize_text(candidate_chunk), _normalize_text(reference_chunk)).ratio()
            token_ratio = _token_overlap_score(candidate_chunk, reference_chunk)
            phrase_ratio = _phrase_overlap_score(candidate_chunk, reference_chunk)
            score = round((sequence_ratio * 0.4) + (token_ratio * 0.35) + (phrase_ratio * 0.25), 4)
            if score > best_score:
                best_score = score
                best_candidate = candidate_chunk
                best_reference = reference_chunk
    return best_score, best_candidate[:180], best_reference[:180]


def _similarity_score(candidate_text: str, reference_text: str) -> float:
    normalized_candidate = _normalize_text(candidate_text)
    normalized_reference = _normalize_text(reference_text)
    if len(normalized_candidate) < 18 or len(normalized_reference) < 18:
        return 0.0
    sequence_ratio = SequenceMatcher(None, normalized_candidate, normalized_reference).ratio()
    token_ratio = _token_overlap_score(normalized_candidate, normalized_reference)
    phrase_ratio = _phrase_overlap_score(normalized_candidate, normalized_reference)
    direct_score = round((sequence_ratio * 0.42) + (token_ratio * 0.33) + (phrase_ratio * 0.25), 4)
    chunk_score = _best_chunk_similarity(candidate_text, reference_text)
    return round(max(direct_score, chunk_score), 4)


def _chunk_text(value: str, *, chunk_size: int = 180) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[\n\r]+|(?<=[.!?])\s+", text) if part.strip()]
    if not parts:
        return [text[:chunk_size]]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if not current:
            current = part
            continue
        candidate = f"{current} {part}".strip()
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current[:chunk_size])
            current = part
    if current:
        chunks.append(current[:chunk_size])
    return chunks


def _best_preview_pair(candidate_text: str, reference_text: str) -> tuple[str, str]:
    candidate_chunks = _chunk_text(candidate_text)
    reference_chunks = _chunk_text(reference_text)
    best_pair = (candidate_text[:180], reference_text[:180])
    best_score = -1.0
    for candidate_chunk in candidate_chunks[:8]:
        for reference_chunk in reference_chunks[:8]:
            score = _similarity_score(candidate_chunk, reference_chunk)
            if score > best_score:
                best_score = score
                best_pair = (candidate_chunk[:180], reference_chunk[:180])
    return best_pair


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "High confidence"
    if confidence >= 0.45:
        return "Moderate confidence"
    return "Low confidence"


def _build_match_reason(
    *,
    similarity_score: float,
    token_overlap: float,
    phrase_overlap: float,
    chunk_similarity: float,
    source_candidate: SourceCandidate,
) -> str:
    reasons: list[str] = []
    if chunk_similarity >= 0.5:
        reasons.append("Strong chunk-level overlap was detected in a submitted answer segment.")
    elif similarity_score >= 0.45:
        reasons.append("A meaningful normalized text similarity pattern was detected.")

    if token_overlap >= 0.35:
        reasons.append("Key technical terms and sequence language overlapped with the reference.")
    if phrase_overlap >= 0.2:
        reasons.append("Short phrase structure aligned with the reference wording.")
    if source_candidate.source_domain:
        reasons.append(f"Reference context came from {source_candidate.source_domain}.")
    elif source_candidate.source_type:
        reasons.append(f"Reference context matched a {source_candidate.source_type.lower()} style source.")

    return " ".join(reasons).strip() or "Observed similarity pattern should be reviewed in context."


def _build_source_candidate(reference: dict[str, Any]) -> SourceCandidate:
    source_url = str(reference.get("source_url") or "").strip() or None
    normalized_text = _normalize_text(reference.get("content") or "")
    return SourceCandidate(
        source_title=str(reference.get("source_title") or "Possible reference"),
        source_url=source_url,
        source_domain=_source_domain(source_url),
        source_type=str(reference.get("source_type") or "Reference"),
        retrieved_from=str(reference.get("retrieved_from") or "controlled_corpus"),
        content_snippet=_clip_text(reference.get("content") or "", max_length=260),
        normalized_text=normalized_text,
        tags=[str(tag) for tag in list(reference.get("tags") or []) if str(tag or "").strip()],
        created_at=str(reference.get("created_at") or "") or None,
    )


@lru_cache(maxsize=1)
def load_reference_corpus() -> list[dict[str, Any]]:
    with CORPUS_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [item for item in data if isinstance(item, dict)]


def _is_validation_mode(assessment_name: str, submitted_answers: list[dict[str, Any]]) -> bool:
    if str(assessment_name or "").strip() == PROVENANCE_VALIDATION_TRACK:
        return True
    return any(str(item.get("assessment_name") or "").strip() == PROVENANCE_VALIDATION_TRACK for item in submitted_answers)


def _reference_relevance_score(reference: dict[str, Any], answer: dict[str, Any], assessment_name: str) -> int:
    score = 0
    answer_assessment_type = str(answer.get("assessment_type") or "").strip().lower()
    reference_assessment_type = str(reference.get("assessment_type") or "").strip().lower()
    if answer_assessment_type and reference_assessment_type == answer_assessment_type:
      score += 6
    question_text = " ".join(
        str(value or "")
        for value in [
            answer.get("question_title"),
            answer.get("section_title"),
            answer.get("category"),
            assessment_name,
        ]
    ).lower()
    for tag in list(reference.get("tags") or []):
        normalized_tag = str(tag or "").strip().lower()
        if normalized_tag and normalized_tag in question_text:
            score += 2
    source_text = " ".join(
        str(value or "")
        for value in [reference.get("source_title"), reference.get("assessment_type")]
    ).lower()
    for token in _tokenize(question_text)[:12]:
        if token and token in source_text:
            score += 1
    return score


def _question_signal_window(question_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not question_id:
        return events
    scoped = []
    for event in events:
        payload = event.get("payload") or {}
        if payload.get("question_id") == question_id:
            scoped.append(event)
    return scoped or events


def _behavioral_correlation(question_id: str, answer_length: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    scoped_events = _question_signal_window(question_id, events)
    sorted_events = sorted(scoped_events, key=lambda event: _parse_iso_seconds(event.get("occurred_at")) or 0.0)
    blur_or_hidden_times = [
        _parse_iso_seconds(event.get("occurred_at"))
        for event in sorted_events
        if str(event.get("event_type") or "").lower() in {"blur", "window_blur"}
        or (
            str(event.get("event_type") or "").lower() == "visibility_change"
            and str((event.get("payload") or {}).get("state") or "").lower() == "hidden"
        )
    ]
    clipboard_events = [
        event for event in sorted_events
        if str(event.get("event_type") or "").lower() == "clipboard"
        or str(event.get("event_type") or "").lower() in {"clipboard_paste", "clipboard_copy"}
    ]
    paste_related_answer_changes = [
        event for event in sorted_events
        if str(event.get("event_type") or "").lower() == "answer_change"
        and str((event.get("payload") or {}).get("change_type") or "").lower() == "paste"
    ]
    typing_recovery_events = [
        event for event in sorted_events
        if str(event.get("event_type") or "").lower() in {"typing_burst", "typing_started", "typing_stopped", "backspace_activity"}
    ]

    signals: list[str] = []
    if clipboard_events:
        signals.append("Paste event detected near the submitted answer.")

    focus_before_insertion = False
    for answer_change in paste_related_answer_changes:
        answer_ts = _parse_iso_seconds(answer_change.get("occurred_at"))
        if answer_ts is None:
            continue
        if any(blur_ts is not None and 0 <= (answer_ts - blur_ts) <= 90 for blur_ts in blur_or_hidden_times):
            focus_before_insertion = True
            break
    if focus_before_insertion:
        signals.append("Focus loss occurred shortly before the answer insertion.")

    if answer_length >= 220:
        signals.append("Large answer insertion was observed for the final response.")

    minimal_edits_after_paste = False
    if paste_related_answer_changes:
        last_paste_ts = max((_parse_iso_seconds(event.get("occurred_at")) or 0.0) for event in paste_related_answer_changes)
        post_paste_typing = [
            event for event in typing_recovery_events
            if (_parse_iso_seconds(event.get("occurred_at")) or 0.0) > last_paste_ts
        ]
        post_paste_input_changes = [
            event for event in sorted_events
            if str(event.get("event_type") or "").lower() == "answer_change"
            and str((event.get("payload") or {}).get("change_type") or "").lower() == "input"
            and (_parse_iso_seconds(event.get("occurred_at")) or 0.0) > last_paste_ts
        ]
        minimal_edits_after_paste = len(post_paste_typing) <= 1 and len(post_paste_input_changes) <= 1
    if minimal_edits_after_paste:
        signals.append("Only limited edits were observed after paste-related insertion.")

    return {
        "signals": signals,
        "signal_count": len(signals),
        "paste_event_detected": bool(clipboard_events),
        "focus_loss_before_insertion": focus_before_insertion,
        "large_answer_insertion": answer_length >= 220,
        "minimal_edits_after_paste": minimal_edits_after_paste,
    }


def _likelihood_from_match(best_score: float, behavioral_signal_count: int) -> str:
    if best_score >= 0.82 and behavioral_signal_count >= 2:
        return HIGH
    if best_score >= 0.72:
        return HIGH if behavioral_signal_count >= 1 else MEDIUM
    if best_score >= 0.56:
        return MEDIUM if behavioral_signal_count >= 1 else LOW
    return LOW


def _confidence_from_match(best_score: float, behavioral_signal_count: int) -> float:
    confidence = (best_score * 0.82) + min(0.18, behavioral_signal_count * 0.05)
    return round(min(0.98, max(0.0, confidence)), 4)


def _match_thresholds(validation_mode: bool) -> tuple[float, float, float]:
    if validation_mode:
        return (0.22, 0.25, 0.55)
    return (0.30, 0.56, 0.72)


def _likelihood_from_score(best_score: float, behavioral_signal_count: int, validation_mode: bool) -> str:
    minimum_match, medium_threshold, high_threshold = _match_thresholds(validation_mode)
    if best_score < minimum_match:
        return LOW
    if validation_mode:
        if best_score > high_threshold:
            return HIGH
        if best_score >= medium_threshold:
            return MEDIUM if behavioral_signal_count < 3 else HIGH
        return LOW
    return _likelihood_from_match(best_score, behavioral_signal_count)


def _confidence_from_score(best_score: float, behavioral_signal_count: int, validation_mode: bool) -> float:
    if validation_mode:
        confidence = (best_score * 0.9) + min(0.16, behavioral_signal_count * 0.04)
        return round(min(0.98, max(0.0, confidence)), 4)
    return _confidence_from_match(best_score, behavioral_signal_count)


def analyze_answer_provenance(
    *,
    attempt_id: str,
    assessment_name: str,
    submitted_answers: Iterable[Dict[str, Any]],
    events: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    answers = [item for item in submitted_answers if str(item.get("answer_text") or "").strip()]
    event_list = list(events or [])
    corpus = load_reference_corpus()
    validation_mode = _is_validation_mode(assessment_name, answers)
    minimum_match_threshold, _, _ = _match_thresholds(validation_mode)
    logger.info(
        "provenance_corpus_loaded attempt_id=%s corpus_entries=%s validation_mode=%s",
        attempt_id,
        len(corpus),
        validation_mode,
    )
    matches: list[dict[str, Any]] = []

    for answer in answers:
        answer_text = str(answer.get("answer_text") or "").strip()
        if len(answer_text) < 20:
            continue

        answer_assessment_type = str(answer.get("assessment_type") or assessment_name or "").strip()
        ranked_references = sorted(
            corpus,
            key=lambda item: _reference_relevance_score(item, answer, assessment_name),
            reverse=True,
        )
        relevant_references = [
            item for item in ranked_references
            if validation_mode or not answer_assessment_type or _reference_relevance_score(item, answer, assessment_name) > 0
        ] or ranked_references

        for reference in relevant_references:
            reference_text = str(reference.get("content") or "").strip()
            similarity = _similarity_score(answer_text, reference_text)
            if similarity < minimum_match_threshold:
                continue
            source_candidate = _build_source_candidate(reference)
            token_overlap = round(_token_overlap_score(answer_text, reference_text), 4)
            phrase_overlap = round(_phrase_overlap_score(answer_text, reference_text), 4)
            chunk_similarity, chunk_candidate_excerpt, chunk_reference_excerpt = _best_chunk_similarity_details(answer_text, reference_text)
            candidate_excerpt, reference_excerpt = _best_preview_pair(answer_text, reference_text)
            matched_candidate_excerpt = chunk_candidate_excerpt or candidate_excerpt
            matched_reference_excerpt = chunk_reference_excerpt or reference_excerpt
            correlation = _behavioral_correlation(str(answer.get("question_id") or ""), len(answer_text), event_list)
            likelihood = _likelihood_from_score(similarity, correlation["signal_count"], validation_mode)
            confidence = _confidence_from_score(similarity, correlation["signal_count"], validation_mode)
            match_evidence = MatchEvidence(
                similarity_score=round(similarity, 4),
                token_overlap=token_overlap,
                phrase_overlap=phrase_overlap,
                chunk_similarity=round(chunk_similarity, 4),
                matched_candidate_excerpt=matched_candidate_excerpt,
                matched_reference_excerpt=matched_reference_excerpt,
                confidence_label=_confidence_label(confidence),
                match_reason=_build_match_reason(
                    similarity_score=similarity,
                    token_overlap=token_overlap,
                    phrase_overlap=phrase_overlap,
                    chunk_similarity=chunk_similarity,
                    source_candidate=source_candidate,
                ),
            )
            matches.append(
                {
                    "attempt_id": attempt_id,
                    "question_id": answer.get("question_id"),
                    "question_title": answer.get("question_title"),
                    "assessment_type": answer_assessment_type,
                    "source_candidate": asdict(source_candidate),
                    "source_title": source_candidate.source_title,
                    "source_type": source_candidate.source_type,
                    "source_url": source_candidate.source_url,
                    "source_domain": source_candidate.source_domain,
                    "retrieved_from": source_candidate.retrieved_from,
                    "content_snippet": source_candidate.content_snippet,
                    "tags": source_candidate.tags,
                    "similarity_score": match_evidence.similarity_score,
                    "similarity_percent": int(round(similarity * 100)),
                    "likelihood": likelihood,
                    "confidence": confidence,
                    "match_evidence": asdict(match_evidence),
                    "token_overlap": match_evidence.token_overlap,
                    "phrase_overlap": match_evidence.phrase_overlap,
                    "chunk_similarity": match_evidence.chunk_similarity,
                    "confidence_label": match_evidence.confidence_label,
                    "match_reason": match_evidence.match_reason,
                    "candidate_excerpt": match_evidence.matched_candidate_excerpt,
                    "reference_excerpt": match_evidence.matched_reference_excerpt,
                    "behavioral_correlation": correlation["signals"],
                    "behavioral_signal_count": correlation["signal_count"],
                    "telemetry_flags": {
                        "paste_event_detected": correlation["paste_event_detected"],
                        "focus_loss_before_insertion": correlation["focus_loss_before_insertion"],
                        "large_answer_insertion": correlation["large_answer_insertion"],
                        "minimal_edits_after_paste": correlation["minimal_edits_after_paste"],
                    },
                }
            )

    matches.sort(
        key=lambda item: (
            0 if item["likelihood"] == HIGH else 1 if item["likelihood"] == MEDIUM else 2,
            -item["similarity_score"],
            -item["behavioral_signal_count"],
        )
    )
    top_matches = matches[:3]
    best_match = top_matches[0] if top_matches else None
    aggregate_behavioral_signals = []
    seen_signals: set[str] = set()
    for match in top_matches:
        for signal in match.get("behavioral_correlation") or []:
            if signal not in seen_signals:
                seen_signals.add(signal)
                aggregate_behavioral_signals.append(signal)

    best_score = _safe_float(best_match.get("similarity_score"), 0.0) if best_match else 0.0
    best_signal_count = int(best_match.get("behavioral_signal_count") or 0) if best_match else 0
    overall_likelihood = _likelihood_from_score(best_score, best_signal_count, validation_mode)
    overall_confidence = _confidence_from_score(best_score, best_signal_count, validation_mode) if best_match else 0.0

    summary = "No meaningful reference overlap was detected in submitted answers."
    if best_match:
        summary = (
            f"Reference overlap detected in {len(top_matches)} submitted answer segment"
            f"{'s' if len(top_matches) != 1 else ''}. Review the similarity in context with nearby behavioral telemetry."
        )
    result = ProvenanceResult(
        likelihood=overall_likelihood,
        confidence=overall_confidence,
        top_matches=top_matches,
        behavioral_correlation=aggregate_behavioral_signals,
        reviewer_summary=summary,
        limitations_note=LIMITATIONS_NOTE,
    )

    return {
        "attempt_id": attempt_id,
        "summary": result.reviewer_summary,
        "external_similarity_likelihood": result.likelihood,
        "confidence_score": result.confidence,
        "possible_reference_matches": result.top_matches,
        "behavioral_correlation": result.behavioral_correlation,
        "matching_segment_preview": {
            "candidate_excerpt": best_match.get("candidate_excerpt") if best_match else "",
            "reference_excerpt": best_match.get("reference_excerpt") if best_match else "",
        },
        "reviewer_summary": result.reviewer_summary,
        "limitations_note": result.limitations_note,
        "top_matches": result.top_matches,
        "evidence_title": "Potential External Similarity Pattern" if result.likelihood in {MEDIUM, HIGH} else None,
    }


def build_provenance_evidence_item(provenance_result: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not provenance_result:
        return None
    likelihood = str(provenance_result.get("external_similarity_likelihood") or LOW).upper()
    if likelihood not in {MEDIUM, HIGH}:
        return None
    possible_matches = list(provenance_result.get("possible_reference_matches") or [])
    top_match = possible_matches[0] if possible_matches else {}
    correlation = list(provenance_result.get("behavioral_correlation") or [])
    details = []
    if top_match.get("source_title"):
        details.append(f"Possible source match: {top_match['source_title']}.")
    if correlation:
        details.append("Behavioral correlation: " + " ".join(correlation))
    return {
        "signal_type": "ANSWER_PROVENANCE",
        "title": "Potential External Similarity Pattern",
        "severity": likelihood,
        "count": max(1, len(possible_matches)),
        "explanation": " ".join(details).strip() or "Reference overlap detected in submitted answers and should be reviewed in context.",
        "first_seen": None,
        "last_seen": None,
        "related_event_count": int(top_match.get("behavioral_signal_count") or 0),
        "reviewer_summary": provenance_result.get("summary") or "Potential external similarity source detected.",
    }
