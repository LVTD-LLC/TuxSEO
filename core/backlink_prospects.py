import re
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

_BLOCKED_DOMAIN_SUFFIXES = (
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "medium.com",
    "substack.com",
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
)

_SHORT_TOKEN_WHITELIST = {"ai", "api", "seo", "ui", "ux"}

_STOPWORDS = {
    "about",
    "after",
    "also",
    "because",
    "from",
    "have",
    "into",
    "more",
    "that",
    "their",
    "them",
    "they",
    "this",
    "what",
    "when",
    "where",
    "with",
    "your",
}

_MIN_EXA_SCORE = 0.15
_MIN_TOPIC_OVERLAP_RATIO = 0.2
_BACKLINK_PROSPECTS_CACHE_TTL_SECONDS = 6 * 60 * 60
_BACKLINK_PROSPECTS_REFRESH_LOCK_TTL_SECONDS = 5 * 60


def get_backlink_prospects_cache_key(project_page_id: int) -> str:
    return f"project-page:{project_page_id}:backlink-prospects-v1"


def get_backlink_prospects_refresh_lock_key(project_page_id: int) -> str:
    return f"project-page:{project_page_id}:backlink-prospects-refresh-lock-v1"


def get_cached_backlink_prospects(project_page_id: int) -> list[dict] | None:
    payload = cache.get(get_backlink_prospects_cache_key(project_page_id))
    if payload is None:
        return None

    candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
    return candidates if isinstance(candidates, list) else []


def set_cached_backlink_prospects(project_page_id: int, candidates: list[dict]) -> None:
    cache.set(
        get_backlink_prospects_cache_key(project_page_id),
        {"candidates": candidates},
        timeout=_BACKLINK_PROSPECTS_CACHE_TTL_SECONDS,
    )


def _tokenize(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{2,}", (value or "").lower())
    return {
        token
        for token in tokens
        if token not in _STOPWORDS and (len(token) >= 4 or token in _SHORT_TOKEN_WHITELIST)
    }


def _normalize_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip(" \t\n\r-•*"))
    return cleaned[:120]


def _registrable_domain(hostname: str) -> str:
    hostname = (hostname or "").lower().strip(".")
    if not hostname:
        return ""

    parts = hostname.split(".")
    if len(parts) <= 2:
        return hostname

    second_level_tlds = {
        "co.uk",
        "org.uk",
        "gov.uk",
        "ac.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
    }
    tail = ".".join(parts[-2:])
    country_tail = ".".join(parts[-3:])

    if tail in second_level_tlds and len(parts) >= 3:
        return country_tail

    return tail


def _split_into_phrases(value: str) -> list[str]:
    if not value:
        return []

    chunks = re.split(r"[\n;,|]", value)
    phrases = []
    for chunk in chunks:
        normalized = _normalize_phrase(chunk)
        if not normalized:
            continue

        token_count = len(re.findall(r"[A-Za-z0-9]+", normalized))
        if token_count < 2:
            continue

        phrases.append(normalized)

    return phrases


def extract_backlink_topics(project, project_page, max_topics: int = 5) -> list[str]:
    """Extract stable topical phrases from project + page metadata."""
    max_topics = max(1, min(8, int(max_topics or 5)))

    field_values = [
        getattr(project_page, "summary", ""),
        getattr(project_page, "title", ""),
        getattr(project_page, "type_ai_guess", ""),
        getattr(project_page, "description", ""),
        getattr(project, "blog_theme", ""),
        getattr(project, "key_features", ""),
        getattr(project, "target_audience_summary", ""),
        getattr(project, "summary", ""),
    ]

    seen = set()
    topics = []

    for value in field_values:
        for phrase in _split_into_phrases(value):
            normalized = phrase.lower()
            if normalized in seen:
                continue

            topic_tokens = _tokenize(phrase)
            if len(topic_tokens) < 2:
                continue

            seen.add(normalized)
            topics.append(phrase)

            if len(topics) >= max_topics:
                return topics

    return topics


def _is_blocked_domain(domain: str) -> bool:
    domain = (domain or "").lower()
    if not domain:
        return True

    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in _BLOCKED_DOMAIN_SUFFIXES)


def _passes_topic_relevance_gate(*, topic: str, title: str, snippet: str, score) -> bool:
    topic_tokens = _tokenize(topic)
    if not topic_tokens:
        return False

    candidate_tokens = _tokenize(f"{title} {snippet}")
    overlap_ratio = len(topic_tokens.intersection(candidate_tokens)) / max(len(topic_tokens), 1)

    try:
        parsed_score = float(score)
    except (TypeError, ValueError):
        parsed_score = 0.0

    if parsed_score < _MIN_EXA_SCORE:
        return False

    return overlap_ratio >= _MIN_TOPIC_OVERLAP_RATIO


def _build_topic_query(topic: str) -> str:
    return f"{topic} best practices guide"


def _search_exa_for_topic(*, exa_api_key: str, topic: str, num_results: int = 6) -> list[dict]:
    response = requests.post(
        "https://api.exa.ai/search",
        headers={
            "x-api-key": exa_api_key,
            "Content-Type": "application/json",
        },
        json={
            "query": _build_topic_query(topic),
            "type": "auto",
            "num_results": max(4, min(12, int(num_results or 6))),
            "contents": {
                "highlights": {
                    "numSentences": 2,
                }
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("results", [])


def discover_backlink_prospects(project_page, max_candidates: int = 8, max_topics: int = 5) -> list[dict]:
    """Discover external backlink prospects for a project page via Exa search."""
    max_candidates = max(1, min(20, int(max_candidates or 8)))
    exa_api_key = (getattr(settings, "EXA_API_KEY", "") or "").strip()

    if not exa_api_key:
        return []

    project = project_page.project
    topics = extract_backlink_topics(project, project_page, max_topics=max_topics)
    if not topics:
        return []

    project_domain = _registrable_domain(urlparse(getattr(project, "url", "")).hostname or "")

    candidates = []
    seen_urls = set()

    try:
        for topic in topics:
            results = _search_exa_for_topic(
                exa_api_key=exa_api_key,
                topic=topic,
                num_results=max(6, max_candidates),
            )

            for item in results:
                url = (item.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue

                parsed_url = urlparse(url)
                if parsed_url.scheme not in {"http", "https"}:
                    continue

                domain = (parsed_url.hostname or "").lower()
                if _is_blocked_domain(domain):
                    continue

                candidate_site = _registrable_domain(domain)
                if project_domain and candidate_site == project_domain:
                    continue

                title = (item.get("title") or "").strip() or domain
                highlights = item.get("highlights") or []
                snippet = " ".join(highlights) if isinstance(highlights, list) else str(highlights)
                snippet = _normalize_phrase(snippet)

                if not _passes_topic_relevance_gate(
                    topic=topic,
                    title=title,
                    snippet=snippet,
                    score=item.get("score"),
                ):
                    continue

                candidates.append(
                    {
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "snippet": snippet,
                        "topic": topic,
                        "source": "exa",
                    }
                )
                seen_urls.add(url)

                if len(candidates) >= max_candidates:
                    return candidates

    except requests.RequestException as error:
        logger.warning(
            "[BacklinkProspects] Exa lookup failed",
            error=str(error),
            project_id=getattr(project, "id", None),
            project_page_id=getattr(project_page, "id", None),
            exc_info=True,
        )
        return []

    return candidates


def refresh_backlink_prospects_cache(project_page) -> list[dict]:
    try:
        candidates = discover_backlink_prospects(project_page)
        set_cached_backlink_prospects(project_page.id, candidates)
        return candidates
    finally:
        cache.delete(get_backlink_prospects_refresh_lock_key(project_page.id))
