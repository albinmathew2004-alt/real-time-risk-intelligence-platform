from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape
import json
import logging
import math
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol
from urllib.parse import urlparse
import requests


CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "provenance_reference_corpus.json"
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
PROVENANCE_VALIDATION_TRACK = "Testing Post-Submission Answer Provenance Analysis"
LIMITATIONS_NOTE = "Source matches indicate potential reference overlap, not definitive proof of copying."
WEB_RETRIEVAL_LIMITATIONS_NOTE = "Web retrieval matches are experimental and may contain approximate or indirect overlaps."
ENABLE_EXPERIMENTAL_WEB_RETRIEVAL = os.getenv("ENABLE_EXPERIMENTAL_WEB_RETRIEVAL", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_SEMANTIC_PROVENANCE = os.getenv("ENABLE_SEMANTIC_PROVENANCE", "false").strip().lower() in {"1", "true", "yes", "on"}
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
SERPER_API_KEY = os.getenv("SERPER_API_KEY", os.getenv("SERPER_SEARCH_API_KEY", "")).strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("OPENAI_EMBEDDING_API_KEY", "")).strip()
WEB_RETRIEVAL_MAX_RESULTS = max(1, int(os.getenv("WEB_RETRIEVAL_MAX_RESULTS", "3") or "3"))
WEB_RETRIEVAL_TIMEOUT_SECONDS = max(1, int(os.getenv("WEB_RETRIEVAL_TIMEOUT_SECONDS", "6") or "6"))
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPER_SEARCH_ENDPOINT = "https://google.serper.dev/search"
logger = logging.getLogger(__name__)
GENERIC_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "can", "could", "describe",
    "do", "does", "during", "each", "explain", "for", "from", "how", "if", "in", "into", "is",
    "it", "its", "may", "might", "of", "on", "or", "should", "show", "that", "the", "their",
    "this", "to", "use", "what", "when", "why", "with", "your",
}
RARE_TERM_STOPWORDS = GENERIC_QUERY_STOPWORDS | {
    "answer", "assessment", "candidate", "demo", "question", "review", "submission",
}


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
    rare_term_overlap: float
    title_snippet_relevance: float
    chunk_similarity: float
    semantic_similarity: float | None
    semantic_provider: str | None
    semantic_enabled: bool
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


@dataclass(slots=True)
class RetrievedWebCandidate:
    source_candidate: SourceCandidate
    retrieval_source: str
    retrieval_confidence: float
    retrieved_at: str
    ranking_position: int | None = None
    query: str | None = None
    extraction_status: str = "not_fetched"


@dataclass(slots=True)
class RetrievalResult:
    enabled: bool
    provider_name: str
    generated_queries: list[str]
    candidates: list[RetrievedWebCandidate]
    retrieved_at: str
    note: str


@dataclass(slots=True)
class EmbeddingResult:
    enabled: bool
    provider_name: str
    vectors: list[list[float] | None]
    dimensions: int
    note: str = ""


class RetrievalProvider(Protocol):
    provider_name: str

    def retrieve(self, queries: list[str], *, limit: int = 5) -> RetrievalResult:
        ...


class EmbeddingProvider(Protocol):
    provider_name: str

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        ...


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


class DisabledEmbeddingProvider:
    provider_name = "semantic_disabled"

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            enabled=False,
            provider_name=self.provider_name,
            vectors=[None for _ in texts],
            dimensions=0,
            note="Semantic provenance is disabled by feature flag.",
        )


class LocalSentenceTransformerProvider:
    provider_name = "local_sentence_transformer"

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            enabled=False,
            provider_name=self.provider_name,
            vectors=[None for _ in texts],
            dimensions=0,
            note="Local sentence-transformer provider is not installed in this environment.",
        )


class OpenAIEmbeddingProvider:
    provider_name = "openai_embeddings"

    def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        if not OPENAI_API_KEY:
            return EmbeddingResult(
                enabled=False,
                provider_name=self.provider_name,
                vectors=[None for _ in texts],
                dimensions=0,
                note="OpenAI embedding provider is not configured.",
            )
        return EmbeddingResult(
            enabled=False,
            provider_name=self.provider_name,
            vectors=[None for _ in texts],
            dimensions=0,
            note="OpenAI embedding provider is reserved for a future semantic provenance integration.",
        )


class SearchQueryBuilder:
    def __init__(self, *, max_queries: int = 3, max_terms: int = 14) -> None:
        self.max_queries = max_queries
        self.max_terms = max_terms

    def build_queries(self, *, answer_text: str, question_title: str = "", assessment_name: str = "") -> list[str]:
        normalized_answer = _normalize_text(answer_text)
        if not normalized_answer:
            return []
        chunks = _prioritized_chunks(normalized_answer, limit=self.max_queries, chunk_size=210)
        prefix_terms = _high_signal_terms(f"{question_title} {assessment_name}", limit=5)
        anchor_phrases = _high_signal_phrases(question_title, limit=2)
        queries: list[str] = []
        for chunk in chunks:
            chunk_terms = _high_signal_terms(chunk, limit=self.max_terms)
            chunk_phrases = _high_signal_phrases(chunk, limit=2)
            merged_terms = []
            seen: set[str] = set()
            for term in [*prefix_terms, *anchor_phrases, *chunk_phrases, *chunk_terms]:
                if term and term not in seen:
                    seen.add(term)
                    merged_terms.append(term)
            query = " ".join(merged_terms[: self.max_terms]).strip()
            if query and query not in queries:
                queries.append(query)
        return queries[: self.max_queries]


class BraveSearchProvider:
    provider_name = "brave_search"

    def retrieve(self, queries: list[str], *, limit: int = 5) -> RetrievalResult:
        if not ENABLE_EXPERIMENTAL_WEB_RETRIEVAL:
            return _disabled_retrieval_result(self.provider_name, queries)
        if not BRAVE_SEARCH_API_KEY:
            return RetrievalResult(
                enabled=True,
                provider_name=self.provider_name,
                generated_queries=queries,
                candidates=[],
                retrieved_at=_utcnow_iso(),
                note="Brave Search API key is not configured. Falling back to controlled corpus matching only.",
            )

        retrieved_at = _utcnow_iso()
        candidates: list[RetrievedWebCandidate] = []
        seen_urls: set[str] = set()
        fetch_stats = {"fetch_success_count": 0, "fetch_failure_count": 0}
        logger.info("web_retrieval_provider_used provider=%s query_count=%s", self.provider_name, len(queries[:3]))
        try:
            for query in queries[:3]:
                response = requests.get(
                    BRAVE_SEARCH_ENDPOINT,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                        "User-Agent": "ProctorIQ/1.0 experimental provenance retrieval",
                    },
                    params={
                        "q": query,
                        "count": min(max(1, limit), 5),
                        "text_decorations": False,
                        "search_lang": "en",
                        "country": "US",
                    },
                    timeout=WEB_RETRIEVAL_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                payload = response.json()
                results = list((((payload or {}).get("web") or {}).get("results") or []))
                for result in results:
                    source_url = str(result.get("url") or "").strip()
                    if not _is_retrievable_url(source_url) or source_url in seen_urls:
                        continue
                    seen_urls.add(source_url)
                    extracted = _fetch_webpage_candidate(
                        url=source_url,
                        source_title=str(result.get("title") or result.get("meta_title") or "Possible web reference"),
                        source_type="Web reference",
                        retrieved_from=self.provider_name,
                        query=query,
                        stats=fetch_stats,
                    )
                    if extracted is None:
                        continue
                    candidates.append(
                        RetrievedWebCandidate(
                            source_candidate=extracted,
                            retrieval_source=self.provider_name,
                            retrieval_confidence=round(_safe_float(result.get("page_age"), 0.0) * 0.0 + 0.62, 4),
                            retrieved_at=retrieved_at,
                            ranking_position=len(candidates) + 1,
                            query=query,
                            extraction_status="content_extracted",
                        )
                    )
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break
        except Exception as exc:
            logger.warning("brave_retrieval_failed provider=%s error=%s", self.provider_name, exc)
            return RetrievalResult(
                enabled=True,
                provider_name=self.provider_name,
                generated_queries=queries,
                candidates=[],
                retrieved_at=retrieved_at,
                note="Brave retrieval failed. Falling back to controlled corpus matching only.",
            )

        logger.info(
            "web_retrieval_urls_retrieved provider=%s url_count=%s fetch_success_count=%s fetch_failure_count=%s",
            self.provider_name,
            len(candidates),
            fetch_stats["fetch_success_count"],
            fetch_stats["fetch_failure_count"],
        )
        return RetrievalResult(
            enabled=True,
            provider_name=self.provider_name,
            generated_queries=queries,
            candidates=candidates[:limit],
            retrieved_at=retrieved_at,
            note="Experimental Brave retrieval completed.",
        )


class SerperSearchProvider:
    provider_name = "serper_search"

    def retrieve(self, queries: list[str], *, limit: int = 5) -> RetrievalResult:
        if not ENABLE_EXPERIMENTAL_WEB_RETRIEVAL:
            return _disabled_retrieval_result(self.provider_name, queries)
        if not SERPER_API_KEY:
            return RetrievalResult(
                enabled=True,
                provider_name=self.provider_name,
                generated_queries=queries,
                candidates=[],
                retrieved_at=_utcnow_iso(),
                note="Serper API key is not configured. Falling back to controlled corpus matching only.",
            )

        retrieved_at = _utcnow_iso()
        candidates: list[RetrievedWebCandidate] = []
        seen_urls: set[str] = set()
        fetch_stats = {"fetch_success_count": 0, "fetch_failure_count": 0}
        logger.info("web_retrieval_provider_used provider=%s query_count=%s", self.provider_name, len(queries[:3]))
        try:
            for query in queries[:3]:
                response = requests.post(
                    SERPER_SEARCH_ENDPOINT,
                    headers={
                        "X-API-KEY": SERPER_API_KEY,
                        "Content-Type": "application/json",
                        "User-Agent": "ProctorIQ/1.0 experimental provenance retrieval",
                    },
                    json={
                        "q": query,
                        "num": min(max(1, limit), 5),
                        "gl": "us",
                        "hl": "en",
                    },
                    timeout=WEB_RETRIEVAL_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                payload = response.json() or {}
                results = list(payload.get("organic") or [])
                for result in results:
                    source_url = str(result.get("link") or "").strip()
                    if not _is_retrievable_url(source_url) or source_url in seen_urls:
                        continue
                    seen_urls.add(source_url)
                    extracted = _fetch_webpage_candidate(
                        url=source_url,
                        source_title=str(result.get("title") or "Possible web reference"),
                        source_type="Web reference",
                        retrieved_from=self.provider_name,
                        query=query,
                        stats=fetch_stats,
                    )
                    if extracted is None:
                        continue
                    candidates.append(
                        RetrievedWebCandidate(
                            source_candidate=extracted,
                            retrieval_source=self.provider_name,
                            retrieval_confidence=0.68,
                            retrieved_at=retrieved_at,
                            ranking_position=len(candidates) + 1,
                            query=query,
                            extraction_status="content_extracted",
                        )
                    )
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break
        except Exception as exc:
            logger.warning("web_retrieval_failed provider=%s error=%s", self.provider_name, exc)
            return RetrievalResult(
                enabled=True,
                provider_name=self.provider_name,
                generated_queries=queries,
                candidates=[],
                retrieved_at=retrieved_at,
                note="Serper retrieval failed. Falling back to controlled corpus matching only.",
            )

        logger.info(
            "web_retrieval_urls_retrieved provider=%s url_count=%s fetch_success_count=%s fetch_failure_count=%s",
            self.provider_name,
            len(candidates),
            fetch_stats["fetch_success_count"],
            fetch_stats["fetch_failure_count"],
        )
        return RetrievalResult(
            enabled=True,
            provider_name=self.provider_name,
            generated_queries=queries,
            candidates=candidates[:limit],
            retrieved_at=retrieved_at,
            note="Experimental Serper retrieval completed.",
        )


class BingSearchProvider:
    provider_name = "bing_search"

    def retrieve(self, queries: list[str], *, limit: int = 5) -> RetrievalResult:
        return _disabled_retrieval_result(self.provider_name, queries)


class InternalCorpusProvider:
    provider_name = "internal_corpus"

    def retrieve(self, queries: list[str], *, limit: int = 5) -> RetrievalResult:
        retrieved_at = _utcnow_iso()
        candidates: list[RetrievedWebCandidate] = []
        if not queries:
            return RetrievalResult(
                enabled=ENABLE_EXPERIMENTAL_WEB_RETRIEVAL,
                provider_name=self.provider_name,
                generated_queries=[],
                candidates=[],
                retrieved_at=retrieved_at,
                note="No retrieval queries were generated.",
            )
        corpus = load_reference_corpus()
        ranked: list[tuple[float, dict[str, Any], str]] = []
        for query in queries:
            for reference in corpus:
                query_score = _similarity_score(
                    query,
                    str(reference.get("content") or ""),
                    source_candidate=_build_source_candidate(reference),
                )
                if query_score <= 0:
                    continue
                ranked.append((query_score, reference, query))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for index, (score, reference, query) in enumerate(ranked[:limit], start=1):
            source_candidate = _build_source_candidate({**reference, "retrieved_from": self.provider_name})
            candidates.append(
                RetrievedWebCandidate(
                    source_candidate=source_candidate,
                    retrieval_source=self.provider_name,
                    retrieval_confidence=round(score, 4),
                    retrieved_at=retrieved_at,
                    ranking_position=index,
                    query=query,
                    extraction_status="corpus_preview",
                )
            )
        return RetrievalResult(
            enabled=ENABLE_EXPERIMENTAL_WEB_RETRIEVAL,
            provider_name=self.provider_name,
            generated_queries=queries,
            candidates=candidates,
            retrieved_at=retrieved_at,
            note="Internal corpus retrieval can be used as a future fallback when live web retrieval is enabled.",
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _disabled_retrieval_result(provider_name: str, queries: list[str]) -> RetrievalResult:
    return RetrievalResult(
        enabled=False,
        provider_name=provider_name,
        generated_queries=queries,
        candidates=[],
        retrieved_at=_utcnow_iso(),
        note="Experimental web retrieval is disabled by feature flag.",
    )


def _active_web_retrieval_providers() -> list[RetrievalProvider]:
    providers: list[RetrievalProvider] = []
    if SERPER_API_KEY:
        providers.append(SerperSearchProvider())
    if BRAVE_SEARCH_API_KEY:
        providers.append(BraveSearchProvider())
    if not providers:
        providers.append(SerperSearchProvider())
        providers.append(BraveSearchProvider())
    logger.info(
        "web_retrieval_provider_selection enabled=%s provider_candidates=%s has_serper_key=%s has_brave_key=%s",
        ENABLE_EXPERIMENTAL_WEB_RETRIEVAL,
        ",".join(provider.provider_name for provider in providers),
        bool(SERPER_API_KEY),
        bool(BRAVE_SEARCH_API_KEY),
    )
    return providers


def _match_origin(retrieved_from: str | None) -> str:
    normalized = str(retrieved_from or "").strip().lower()
    if normalized in {"controlled_corpus", "internal_corpus", ""}:
        return "controlled_corpus"
    return "web_retrieval"


def _is_retrievable_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


def _extract_text_from_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<!--.*?-->", " ", html)
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<svg[^>]*>.*?</svg>", " ", text)
    text = re.sub(r"(?is)<iframe[^>]*>.*?</iframe>", " ", text)
    text = re.sub(r"(?is)<(nav|footer|header|aside|form|button|label|select|option)[^>]*>.*?</\1>", " ", text)

    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    meta_description_match = re.search(
        r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\'][^>]*>',
        text,
    )

    block_fragments = re.findall(
        r"(?is)<(?:article|main|section|p|li|pre|code|blockquote|h1|h2|h3|td)[^>]*>(.*?)</(?:article|main|section|p|li|pre|code|blockquote|h1|h2|h3|td)>",
        text,
    )
    extracted_blocks = [
        re.sub(r"\s+", " ", unescape(re.sub(r"(?is)<[^>]+>", " ", fragment))).strip()
        for fragment in block_fragments
    ]
    extracted_blocks = [fragment for fragment in extracted_blocks if len(fragment) >= 24]

    fallback_text = re.sub(r"(?is)<[^>]+>", " ", text)
    fallback_text = re.sub(r"\s+", " ", unescape(fallback_text)).strip()

    composed_parts: list[str] = []
    if title_match:
        composed_parts.append(re.sub(r"\s+", " ", unescape(title_match.group(1))).strip())
    if meta_description_match:
        composed_parts.append(re.sub(r"\s+", " ", unescape(meta_description_match.group(1))).strip())
    composed_parts.extend(extracted_blocks[:40])
    if not composed_parts and fallback_text:
        composed_parts.append(fallback_text)

    composed = "\n\n".join(part for part in composed_parts if part)
    composed = re.sub(r"\n{3,}", "\n\n", composed).strip()
    if len(composed) < 120 and fallback_text:
        return fallback_text
    return composed


def _fetch_webpage_candidate(
    *,
    url: str,
    source_title: str,
    source_type: str,
    retrieved_from: str,
    query: str,
    stats: dict[str, int] | None = None,
) -> SourceCandidate | None:
    if not _is_retrievable_url(url):
        if stats is not None:
            stats["fetch_failure_count"] = stats.get("fetch_failure_count", 0) + 1
        return None
    try:
        response = requests.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (compatible; ProctorIQ/1.0; +https://proctoriq.local/experimental)",
            },
            timeout=WEB_RETRIEVAL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        if stats is not None:
            stats["fetch_failure_count"] = stats.get("fetch_failure_count", 0) + 1
        logger.info("web_retrieval_page_fetch_failed url=%s error=%s", url, exc)
        return None

    content_type = str(response.headers.get("Content-Type") or "").lower()
    if "html" not in content_type and "text/" not in content_type:
        if stats is not None:
            stats["fetch_failure_count"] = stats.get("fetch_failure_count", 0) + 1
        return None

    extracted_text = _extract_text_from_html(response.text or "")
    normalized_text = _normalize_text(extracted_text)
    if len(normalized_text) < 60:
        if stats is not None:
            stats["fetch_failure_count"] = stats.get("fetch_failure_count", 0) + 1
        return None

    if stats is not None:
        stats["fetch_success_count"] = stats.get("fetch_success_count", 0) + 1

    return SourceCandidate(
        source_title=source_title or "Possible web reference",
        source_url=url,
        source_domain=_source_domain(url),
        source_type=source_type,
        retrieved_from=retrieved_from,
        content_snippet=_clip_text(extracted_text, max_length=260),
        normalized_text=normalized_text,
        tags=[tag for tag in _tokenize(query)[:6] if tag],
        created_at=_utcnow_iso(),
    )


def _future_page_content_extraction(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "status": "disabled",
        "content": None,
        "note": "Page-content extraction hook is reserved for a future retrieval pipeline.",
    }


def _future_vector_search_hook(text: str) -> dict[str, Any]:
    return {
        "query_text_length": len(_normalize_text(text)),
        "status": "disabled",
        "matches": [],
        "note": "Vector-search integration hook is reserved for future provenance retrieval experiments.",
    }


def _future_vector_db_lookup_hook(text: str) -> dict[str, Any]:
    return {
        "query_text_length": len(_normalize_text(text)),
        "status": "disabled",
        "matches": [],
        "note": "Vector database integration is reserved for future semantic provenance retrieval.",
    }


def _active_embedding_provider() -> EmbeddingProvider:
    if not ENABLE_SEMANTIC_PROVENANCE:
        return DisabledEmbeddingProvider()
    if OPENAI_API_KEY:
        return OpenAIEmbeddingProvider()
    return LocalSentenceTransformerProvider()


def _cosine_similarity(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(l * r for l, r in zip(left, right))
    left_norm = math.sqrt(sum(l * l for l in left))
    right_norm = math.sqrt(sum(r * r for r in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def _semantic_embedding_chunks(text: str, *, limit: int = 6, chunk_size: int = 220) -> list[str]:
    return _prioritized_chunks(text, limit=limit, chunk_size=chunk_size)


def _semantic_similarity_details(
    candidate_text: str,
    reference_text: str,
) -> dict[str, Any]:
    provider = _active_embedding_provider()
    if not ENABLE_SEMANTIC_PROVENANCE:
        return {
            "semantic_similarity": None,
            "semantic_provider": provider.provider_name,
            "semantic_enabled": False,
        }

    candidate_chunks = _semantic_embedding_chunks(candidate_text, limit=4)
    reference_chunks = _semantic_embedding_chunks(reference_text, limit=4)
    if not candidate_chunks or not reference_chunks:
        return {
            "semantic_similarity": None,
            "semantic_provider": provider.provider_name,
            "semantic_enabled": False,
        }

    texts = [*candidate_chunks, *reference_chunks]
    try:
        embedding_result = provider.embed_texts(texts)
    except Exception as exc:
        logger.info("semantic_provenance_provider_failed provider=%s error=%s", provider.provider_name, exc)
        return {
            "semantic_similarity": None,
            "semantic_provider": provider.provider_name,
            "semantic_enabled": False,
        }

    if not embedding_result.enabled:
        return {
            "semantic_similarity": None,
            "semantic_provider": embedding_result.provider_name,
            "semantic_enabled": False,
        }

    candidate_vectors = embedding_result.vectors[: len(candidate_chunks)]
    reference_vectors = embedding_result.vectors[len(candidate_chunks):]
    best_similarity: float | None = None
    for candidate_vector in candidate_vectors:
        for reference_vector in reference_vectors:
            score = _cosine_similarity(candidate_vector, reference_vector)
            if score is None:
                continue
            if best_similarity is None or score > best_similarity:
                best_similarity = score
    return {
        "semantic_similarity": round(best_similarity, 4) if best_similarity is not None else None,
        "semantic_provider": embedding_result.provider_name,
        "semantic_enabled": best_similarity is not None,
    }


def _build_match_payload(
    *,
    attempt_id: str,
    answer: dict[str, Any],
    assessment_name: str,
    source_candidate: SourceCandidate,
    similarity: float,
    answer_text: str,
    reference_text: str,
    event_list: list[dict[str, Any]],
    validation_mode: bool,
    retrieval_confidence: float | None = None,
    retrieval_timestamp: str | None = None,
    retrieval_source: str | None = None,
) -> dict[str, Any]:
    token_overlap = round(_token_overlap_score(answer_text, reference_text), 4)
    phrase_overlap = round(_phrase_overlap_score(answer_text, reference_text), 4)
    rare_term_overlap = round(_rare_term_overlap_score(answer_text, reference_text), 4)
    title_snippet_relevance = round(
        _title_snippet_relevance_score(
            answer_text,
            source_candidate,
            question_title=str(answer.get("question_title") or ""),
        ),
        4,
    )
    semantic_details = _semantic_similarity_details(answer_text, reference_text)
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
        rare_term_overlap=rare_term_overlap,
        title_snippet_relevance=title_snippet_relevance,
        chunk_similarity=round(chunk_similarity, 4),
        semantic_similarity=semantic_details["semantic_similarity"],
        semantic_provider=semantic_details["semantic_provider"],
        semantic_enabled=semantic_details["semantic_enabled"],
        matched_candidate_excerpt=matched_candidate_excerpt,
        matched_reference_excerpt=matched_reference_excerpt,
        confidence_label=_confidence_label(confidence),
        match_reason=_build_match_reason(
            similarity_score=similarity,
            token_overlap=token_overlap,
            phrase_overlap=phrase_overlap,
            rare_term_overlap=rare_term_overlap,
            title_snippet_relevance=title_snippet_relevance,
            chunk_similarity=chunk_similarity,
            source_candidate=source_candidate,
            behavioral_correlation=correlation["signals"],
        ),
    )
    return {
        "attempt_id": attempt_id,
        "question_id": answer.get("question_id"),
        "question_title": answer.get("question_title"),
        "assessment_type": str(answer.get("assessment_type") or assessment_name or "").strip(),
        "source_candidate": asdict(source_candidate),
        "source_title": source_candidate.source_title,
        "source_type": source_candidate.source_type,
        "source_url": source_candidate.source_url,
        "source_domain": source_candidate.source_domain,
        "retrieved_from": source_candidate.retrieved_from,
        "match_origin": _match_origin(source_candidate.retrieved_from),
        "retrieval_source": retrieval_source or source_candidate.retrieved_from,
        "retrieval_confidence": round(_safe_float(retrieval_confidence, similarity), 4),
        "retrieval_timestamp": retrieval_timestamp,
        "content_snippet": source_candidate.content_snippet,
        "tags": source_candidate.tags,
        "similarity_score": match_evidence.similarity_score,
        "similarity_percent": int(round(similarity * 100)),
        "likelihood": likelihood,
        "confidence": confidence,
        "match_evidence": asdict(match_evidence),
        "token_overlap": match_evidence.token_overlap,
        "phrase_overlap": match_evidence.phrase_overlap,
        "rare_term_overlap": match_evidence.rare_term_overlap,
        "title_snippet_relevance": match_evidence.title_snippet_relevance,
        "chunk_similarity": match_evidence.chunk_similarity,
        "semantic_similarity": match_evidence.semantic_similarity,
        "semantic_provider": match_evidence.semantic_provider,
        "semantic_enabled": match_evidence.semantic_enabled,
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


def _build_retrieval_candidates_for_answer(
    *,
    answer: dict[str, Any],
    assessment_name: str,
    query_builder: SearchQueryBuilder,
    providers: list[RetrievalProvider],
    limit: int = WEB_RETRIEVAL_MAX_RESULTS,
) -> list[RetrievalResult]:
    queries = query_builder.build_queries(
        answer_text=str(answer.get("answer_text") or ""),
        question_title=str(answer.get("question_title") or ""),
        assessment_name=assessment_name,
    )
    if not queries:
        return []
    results: list[RetrievalResult] = []
    for provider in providers:
        results.append(provider.retrieve(queries, limit=min(limit, WEB_RETRIEVAL_MAX_RESULTS)))
    return results


def _experimental_retrieval_hooks(
    *,
    answers: list[dict[str, Any]],
    assessment_name: str,
) -> dict[str, Any]:
    query_builder = SearchQueryBuilder()
    generated_queries = [
        query
        for answer in answers[:2]
        for query in query_builder.build_queries(
            answer_text=str(answer.get("answer_text") or ""),
            question_title=str(answer.get("question_title") or ""),
            assessment_name=assessment_name,
        )
    ]
    if not ENABLE_EXPERIMENTAL_WEB_RETRIEVAL:
        return {
            "enabled": False,
            "generated_at": _utcnow_iso(),
            "generated_queries": generated_queries[:6],
            "results": [],
            "page_content_extraction": _future_page_content_extraction(""),
            "vector_search": _future_vector_search_hook(
                " ".join(str(answer.get("answer_text") or "") for answer in answers[:1])
            ),
            "vector_db": _future_vector_db_lookup_hook(
                " ".join(str(answer.get("answer_text") or "") for answer in answers[:1])
            ),
            "limitations_note": WEB_RETRIEVAL_LIMITATIONS_NOTE,
        }
    providers: list[RetrievalProvider] = [
        InternalCorpusProvider(),
        *_active_web_retrieval_providers(),
        BingSearchProvider(),
    ]
    retrieval_results = [
        asdict(result)
        for answer in answers[:2]
        for result in _build_retrieval_candidates_for_answer(
            answer=answer,
            assessment_name=assessment_name,
            query_builder=query_builder,
            providers=providers,
        )
    ]
    sample_url = None
    for result in retrieval_results:
        candidates = list(result.get("candidates") or [])
        if candidates:
            sample_url = ((candidates[0] or {}).get("source_candidate") or {}).get("source_url")
            if sample_url:
                break
    return {
        "enabled": ENABLE_EXPERIMENTAL_WEB_RETRIEVAL,
        "generated_at": _utcnow_iso(),
        "generated_queries": generated_queries[:6],
        "results": retrieval_results,
        "page_content_extraction": _future_page_content_extraction(sample_url or ""),
        "vector_search": _future_vector_search_hook(
            " ".join(str(answer.get("answer_text") or "") for answer in answers[:1])
        ),
        "vector_db": _future_vector_db_lookup_hook(
            " ".join(str(answer.get("answer_text") or "") for answer in answers[:1])
        ),
        "limitations_note": WEB_RETRIEVAL_LIMITATIONS_NOTE,
    }


def _retrieved_web_matches_for_answers(
    *,
    attempt_id: str,
    answers: list[dict[str, Any]],
    assessment_name: str,
    event_list: list[dict[str, Any]],
    validation_mode: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retrieval_hooks = _experimental_retrieval_hooks(answers=answers, assessment_name=assessment_name)
    if not ENABLE_EXPERIMENTAL_WEB_RETRIEVAL:
        return [], retrieval_hooks

    minimum_match_threshold, _, _ = _match_thresholds(validation_mode)
    matches: list[dict[str, Any]] = []
    providers = _active_web_retrieval_providers()
    for answer in answers:
        answer_text = str(answer.get("answer_text") or "").strip()
        if len(answer_text) < 20:
            continue
        retrieval_results = _build_retrieval_candidates_for_answer(
            answer=answer,
            assessment_name=assessment_name,
            query_builder=SearchQueryBuilder(),
            providers=providers,
        )
        for retrieval in retrieval_results:
            logger.info(
                "web_retrieval_result provider=%s retrieved_url_count=%s query_count=%s",
                retrieval.provider_name,
                len(retrieval.candidates),
                len(retrieval.generated_queries),
            )
            for candidate in retrieval.candidates:
                reference_text = candidate.source_candidate.normalized_text or ""
                similarity = _similarity_score(
                    answer_text,
                    reference_text,
                    source_candidate=candidate.source_candidate,
                    question_title=str(answer.get("question_title") or ""),
                )
                if similarity < minimum_match_threshold:
                    continue
                matches.append(
                    _build_match_payload(
                        attempt_id=attempt_id,
                        answer=answer,
                        assessment_name=assessment_name,
                        source_candidate=candidate.source_candidate,
                        similarity=similarity,
                        answer_text=answer_text,
                        reference_text=reference_text,
                        event_list=event_list,
                        validation_mode=validation_mode,
                        retrieval_confidence=candidate.retrieval_confidence,
                        retrieval_timestamp=candidate.retrieved_at,
                        retrieval_source=candidate.retrieval_source,
                    )
                )
    return matches, retrieval_hooks


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


def _high_signal_terms(value: Any, *, limit: int = 12) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _tokenize(value):
        if len(token) < 3 or token in GENERIC_QUERY_STOPWORDS:
            continue
        technical = any(ch.isdigit() for ch in token) or any(symbol in token for symbol in ("_", ".", "/", "#", ":", "-", "sql", "api", "dom", "jwt"))
        if not technical and len(token) < 5:
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _high_signal_phrases(value: Any, *, limit: int = 4) -> list[str]:
    tokens = [token for token in _tokenize(value) if len(token) >= 3 and token not in GENERIC_QUERY_STOPWORDS]
    phrases: list[str] = []
    seen: set[str] = set()
    for size in (3, 2):
        for index in range(max(0, len(tokens) - size + 1)):
            phrase_tokens = tokens[index:index + size]
            if sum(1 for token in phrase_tokens if token in RARE_TERM_STOPWORDS) >= size:
                continue
            phrase = " ".join(phrase_tokens)
            if phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
            if len(phrases) >= limit:
                return phrases
    return phrases


def _rare_term_overlap_score(candidate_text: str, reference_text: str) -> float:
    candidate_terms = {token for token in _tokenize(candidate_text) if len(token) >= 5 and token not in RARE_TERM_STOPWORDS}
    reference_terms = {token for token in _tokenize(reference_text) if len(token) >= 5 and token not in RARE_TERM_STOPWORDS}
    if not candidate_terms or not reference_terms:
        return 0.0
    overlap = candidate_terms & reference_terms
    if not overlap:
        return 0.0
    return len(overlap) / max(1, min(len(candidate_terms), len(reference_terms)))


def _title_snippet_relevance_score(
    candidate_text: str,
    source_candidate: SourceCandidate | None,
    *,
    question_title: str = "",
) -> float:
    if not source_candidate:
        return 0.0
    source_context = " ".join(
        part for part in [
            source_candidate.source_title,
            source_candidate.content_snippet,
            question_title,
            " ".join(source_candidate.tags or []),
        ] if part
    )
    if not source_context.strip():
        return 0.0
    token_score = _token_overlap_score(candidate_text, source_context)
    phrase_score = _phrase_overlap_score(candidate_text, source_context)
    rare_score = _rare_term_overlap_score(candidate_text, source_context)
    return round((token_score * 0.45) + (phrase_score * 0.25) + (rare_score * 0.30), 4)


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
    candidate_chunks = _prioritized_chunks(candidate_text, limit=10, chunk_size=220)
    reference_chunks = _prioritized_chunks(reference_text, limit=10, chunk_size=220)
    best_score = 0.0
    for candidate_chunk in candidate_chunks or [candidate_text]:
        for reference_chunk in reference_chunks or [reference_text]:
            sequence_ratio = SequenceMatcher(None, _normalize_text(candidate_chunk), _normalize_text(reference_chunk)).ratio()
            token_ratio = _token_overlap_score(candidate_chunk, reference_chunk)
            phrase_ratio = _phrase_overlap_score(candidate_chunk, reference_chunk)
            rare_term_ratio = _rare_term_overlap_score(candidate_chunk, reference_chunk)
            score = round((sequence_ratio * 0.32) + (token_ratio * 0.28) + (phrase_ratio * 0.22) + (rare_term_ratio * 0.18), 4)
            if score > best_score:
                best_score = score
    return best_score


def _best_chunk_similarity_details(candidate_text: str, reference_text: str) -> tuple[float, str, str]:
    candidate_chunks = _prioritized_chunks(candidate_text, limit=10, chunk_size=220) or [_clip_text(candidate_text, max_length=220)]
    reference_chunks = _prioritized_chunks(reference_text, limit=10, chunk_size=220) or [_clip_text(reference_text, max_length=220)]
    best_score = 0.0
    best_candidate = candidate_chunks[0]
    best_reference = reference_chunks[0]
    for candidate_chunk in candidate_chunks:
        for reference_chunk in reference_chunks:
            sequence_ratio = SequenceMatcher(None, _normalize_text(candidate_chunk), _normalize_text(reference_chunk)).ratio()
            token_ratio = _token_overlap_score(candidate_chunk, reference_chunk)
            phrase_ratio = _phrase_overlap_score(candidate_chunk, reference_chunk)
            rare_term_ratio = _rare_term_overlap_score(candidate_chunk, reference_chunk)
            score = round((sequence_ratio * 0.32) + (token_ratio * 0.28) + (phrase_ratio * 0.22) + (rare_term_ratio * 0.18), 4)
            if score > best_score:
                best_score = score
                best_candidate = candidate_chunk
                best_reference = reference_chunk
    return best_score, best_candidate[:180], best_reference[:180]


def _similarity_score(
    candidate_text: str,
    reference_text: str,
    *,
    source_candidate: SourceCandidate | None = None,
    question_title: str = "",
) -> float:
    normalized_candidate = _normalize_text(candidate_text)
    normalized_reference = _normalize_text(reference_text)
    if len(normalized_candidate) < 18 or len(normalized_reference) < 18:
        return 0.0
    sequence_ratio = SequenceMatcher(None, normalized_candidate, normalized_reference).ratio()
    token_ratio = _token_overlap_score(normalized_candidate, normalized_reference)
    phrase_ratio = _phrase_overlap_score(normalized_candidate, normalized_reference)
    rare_term_ratio = _rare_term_overlap_score(normalized_candidate, normalized_reference)
    title_relevance = _title_snippet_relevance_score(candidate_text, source_candidate, question_title=question_title)
    direct_score = round(
        (sequence_ratio * 0.27)
        + (token_ratio * 0.24)
        + (phrase_ratio * 0.21)
        + (rare_term_ratio * 0.18)
        + (title_relevance * 0.10),
        4,
    )
    chunk_score = _best_chunk_similarity(candidate_text, reference_text)
    return round(max(direct_score, chunk_score), 4)


def _chunk_text(value: str, *, chunk_size: int = 180) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    raw_parts = [part.strip() for part in re.split(r"[\n\r]+|(?<=[.!?])\s+", text) if part.strip()]
    parts: list[str] = []
    for part in raw_parts:
        if len(part) > chunk_size:
            token_parts = [token.strip() for token in re.split(r"(?<=,)\s+|(?<=;)\s+|(?<=:)\s+", part) if token.strip()]
            if len(token_parts) > 1:
                parts.extend(token_parts)
                continue
        parts.append(part)
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
    return [chunk for chunk in chunks if len(_normalize_text(chunk)) >= 24] or chunks


def _chunk_quality_score(chunk: str) -> float:
    normalized = _normalize_text(chunk)
    if len(normalized) < 24:
        return 0.0
    token_count = len(_tokenize(normalized))
    phrase_count = len(_high_signal_phrases(normalized, limit=4))
    rare_count = len({token for token in _tokenize(normalized) if len(token) >= 5 and token not in RARE_TERM_STOPWORDS})
    punctuation_bonus = 1.0 if any(mark in chunk for mark in ("(", ")", ":", ".", "/", "_", "#")) else 0.0
    return (min(token_count, 30) * 0.04) + (phrase_count * 0.45) + (min(rare_count, 10) * 0.18) + punctuation_bonus


def _prioritized_chunks(value: str, *, limit: int = 8, chunk_size: int = 200) -> list[str]:
    chunks = _chunk_text(value, chunk_size=chunk_size)
    if not chunks:
        return []
    ranked = sorted(
        enumerate(chunks),
        key=lambda item: (_chunk_quality_score(item[1]), -item[0]),
        reverse=True,
    )
    selected = [chunk for _, chunk in ranked[:limit]]
    return selected


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
    rare_term_overlap: float,
    title_snippet_relevance: float,
    chunk_similarity: float,
    source_candidate: SourceCandidate,
    behavioral_correlation: list[str] | None = None,
) -> str:
    reasons: list[str] = []
    if chunk_similarity >= 0.58:
        reasons.append("Candidate excerpt closely matches a reference chunk.")
    elif chunk_similarity >= 0.46:
        reasons.append("Meaningful chunk-level overlap was detected in the submitted answer.")
    if phrase_overlap >= 0.24:
        reasons.append("Strong phrase overlap with submitted answer.")
    elif similarity_score >= 0.45:
        reasons.append("A meaningful normalized text similarity pattern was detected.")

    if token_overlap >= 0.35:
        reasons.append("Technical terms overlap with the retrieved source.")
    if rare_term_overlap >= 0.18:
        reasons.append("Rare technical terms aligned across the answer and reference.")
    if title_snippet_relevance >= 0.22:
        reasons.append("Source title or snippet context was relevant to the submitted answer.")
    if phrase_overlap >= 0.16 and all("phrase overlap" not in reason.lower() for reason in reasons):
        reasons.append("Short phrase structure aligned with the reference wording.")
    if source_candidate.source_domain:
        reasons.append(f"Reference context came from {source_candidate.source_domain}.")
    elif source_candidate.source_type:
        reasons.append(f"Reference context matched a {source_candidate.source_type.lower()} style source.")
    if behavioral_correlation:
        reasons.append(f"Behavioral correlation: {behavioral_correlation[0]}")

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


def _match_sort_key(item: dict[str, Any]) -> tuple[int, float, float, int, int]:
    likelihood = str(item.get("likelihood") or LOW).upper()
    likelihood_rank = 0 if likelihood == HIGH else 1 if likelihood == MEDIUM else 2
    similarity_score = _safe_float(item.get("similarity_score"), 0.0)
    match_origin = str(item.get("match_origin") or "controlled_corpus").strip().lower()
    retrieval_confidence = _safe_float(item.get("retrieval_confidence"), 0.0)
    source_bonus = 0.025 if match_origin == "web_retrieval" else 0.0
    confidence_bonus = min(0.01, retrieval_confidence * 0.01) if match_origin == "web_retrieval" else 0.0
    return (
        likelihood_rank,
        -(similarity_score + source_bonus + confidence_bonus),
        -similarity_score,
        0 if match_origin == "web_retrieval" else 1,
        -int(item.get("behavioral_signal_count") or 0),
    )


def _dedupe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(matches, key=_match_sort_key)
    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    seen_domains_by_question: set[tuple[str, str]] = set()
    for item in ranked:
        question_id = str(item.get("question_id") or "")
        source_url = str(item.get("source_url") or "").strip().lower()
        source_domain = str(item.get("source_domain") or "").strip().lower()
        source_title = str(item.get("source_title") or "").strip().lower()
        base_key = (question_id, source_url, source_title)
        domain_key = (question_id, source_domain)
        if source_url and base_key in seen_keys:
            continue
        if source_domain and domain_key in seen_domains_by_question:
            continue
        if source_url:
            seen_keys.add(base_key)
        if source_domain:
            seen_domains_by_question.add(domain_key)
        deduped.append(item)
    return deduped


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
            similarity = _similarity_score(
                answer_text,
                reference_text,
                source_candidate=source_candidate,
                question_title=str(answer.get("question_title") or ""),
            )
            if similarity < minimum_match_threshold:
                continue
            source_candidate = _build_source_candidate(reference)
            matches.append(
                _build_match_payload(
                    attempt_id=attempt_id,
                    answer=answer,
                    assessment_name=answer_assessment_type or assessment_name,
                    source_candidate=source_candidate,
                    similarity=similarity,
                    answer_text=answer_text,
                    reference_text=reference_text,
                    event_list=event_list,
                    validation_mode=validation_mode,
                    retrieval_confidence=similarity,
                    retrieval_timestamp=None,
                    retrieval_source=source_candidate.retrieved_from,
                )
            )

    retrieved_matches, retrieval_hooks = _retrieved_web_matches_for_answers(
        attempt_id=attempt_id,
        answers=answers,
        assessment_name=assessment_name,
        event_list=event_list,
        validation_mode=validation_mode,
    )
    matches.extend(retrieved_matches)
    matches = _dedupe_matches(matches)
    matches.sort(key=_match_sort_key)
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
    logger.info(
        "provenance_analysis_summary attempt_id=%s corpus_match_count=%s web_match_count=%s top_match_origin=%s top_match_provider=%s",
        attempt_id,
        len([item for item in matches if str(item.get("match_origin") or "controlled_corpus") == "controlled_corpus"]),
        len([item for item in matches if str(item.get("match_origin") or "") == "web_retrieval"]),
        best_match.get("match_origin") if best_match else "none",
        best_match.get("retrieval_source") if best_match else "none",
    )

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
        "semantic_provenance": {
            "enabled": ENABLE_SEMANTIC_PROVENANCE,
            "provider": _active_embedding_provider().provider_name,
        },
        "experimental_web_retrieval": retrieval_hooks,
        "web_retrieval_disclaimer": WEB_RETRIEVAL_LIMITATIONS_NOTE if ENABLE_EXPERIMENTAL_WEB_RETRIEVAL else "",
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
