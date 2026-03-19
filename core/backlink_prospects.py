import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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

_DEFAULT_SCORING_CONFIG = {
    "MIN_EXA_SCORE": 0.15,
    "MIN_TOPIC_OVERLAP_RATIO": 0.2,
    "MIN_RELEVANCE_SCORE": 0.45,
    "MAX_CANDIDATES": 20,
    "MAX_TOPICS": 8,
    "CACHE_TTL_SECONDS": 6 * 60 * 60,
    "REFRESH_LOCK_TTL_SECONDS": 5 * 60,
    "SCORING_WEIGHTS": {
        "topic_match": 0.45,
        "content_type_fit": 0.2,
        "domain_credibility": 0.2,
        "freshness": 0.15,
    },
}

_CREDIBILITY_BONUS_DOMAINS = {
    "google.com": 1.0,
    "developers.google.com": 1.0,
    "developer.mozilla.org": 1.0,
    "github.com": 0.95,
    "wikipedia.org": 0.9,
}

_CREDIBILITY_LOW_SIGNAL_SUBDOMAIN_PREFIXES = {
    "forum",
    "community",
    "support",
    "m",
    "amp",
}

_LOW_SIGNAL_TITLE_PATTERNS = (
    r"\blog\s*home",
    r"\bhome\b",
    r"\barchive\b",
    r"\btag\b",
    r"\bcategory\b",
    r"\bauthor\b",
    r"\bpage\s*\d+\b",
    r"\bsearch results\b",
)

_LOW_SIGNAL_PATH_PATTERNS = (
    "/tag/",
    "/tags/",
    "/category/",
    "/categories/",
    "/author/",
    "/authors/",
    "/search",
    "/login",
    "/signup",
    "/sign-up",
    "/register",
    "/feed",
)

_HIGH_SIGNAL_CONTENT_HINTS = {
    "article",
    "guide",
    "tutorial",
    "docs",
    "documentation",
    "resources",
    "playbook",
    "checklist",
    "learn",
    "how to",
    "case study",
    "whitepaper",
}

_CONTENT_TYPE_HINTS = {
    "blog": {"blog", "article", "guide", "how to", "tutorial", "learn", "post"},
    "product": {"product", "feature", "features", "platform", "tool", "software", "solution"},
    "documentation": {"docs", "documentation", "api", "reference", "developer"},
    "landing": {"pricing", "plan", "demo", "compare", "why", "overview"},
}

_QUERY_STRIP_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source")


def _get_scoring_config() -> dict:
    configured = getattr(settings, "BACKLINK_PROSPECTS_CONFIG", None) or {}

    merged = {
        **_DEFAULT_SCORING_CONFIG,
        **{k: v for k, v in configured.items() if k != "SCORING_WEIGHTS"},
    }
    merged["SCORING_WEIGHTS"] = {
        **_DEFAULT_SCORING_CONFIG["SCORING_WEIGHTS"],
        **(configured.get("SCORING_WEIGHTS") or {}),
    }
    return merged


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
    config = _get_scoring_config()
    cache.set(
        get_backlink_prospects_cache_key(project_page_id),
        {"candidates": candidates},
        timeout=int(config["CACHE_TTL_SECONDS"]),
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
    return cleaned[:220]


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
    config = _get_scoring_config()
    max_topics = max(1, min(int(config["MAX_TOPICS"]), int(max_topics or 5)))

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

    return any(
        domain == suffix or domain.endswith(f".{suffix}")
        for suffix in _BLOCKED_DOMAIN_SUFFIXES
    )


def _normalize_candidate_url(url: str) -> str:
    if not url:
        return ""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    path = re.sub(r"//+", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    normalized_qs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        key_lower = key.lower()
        if any(key_lower.startswith(prefix) for prefix in _QUERY_STRIP_PREFIXES):
            continue
        normalized_qs.append((key_lower, value.strip()))

    normalized_qs.sort(key=lambda item: (item[0], item[1]))

    return urlunparse(
        (
            parsed.scheme.lower(),
            hostname,
            path or "/",
            "",
            urlencode(normalized_qs, doseq=True),
            "",
        )
    )


def _is_low_signal_page(*, normalized_url: str, title: str, snippet: str) -> bool:
    if not normalized_url:
        return True

    parsed = urlparse(normalized_url)
    path_lower = (parsed.path or "").lower()

    if any(pattern in path_lower for pattern in _LOW_SIGNAL_PATH_PATTERNS):
        return True

    title_lower = (title or "").strip().lower()
    if title_lower and any(
        re.search(pattern, title_lower) for pattern in _LOW_SIGNAL_TITLE_PATTERNS
    ):
        snippet_tokens = _tokenize(snippet)
        if len(snippet_tokens) < 8:
            return True

    signal_haystack = f"{title_lower} {(snippet or '').lower()} {path_lower}"
    has_high_signal_hint = any(
        hint in signal_haystack for hint in _HIGH_SIGNAL_CONTENT_HINTS
    )
    if not has_high_signal_hint and len(_tokenize(f"{title} {snippet}")) < 6:
        return True

    return False


def _compute_topic_match_strength(
    *, topic: str, title: str, snippet: str, exa_score
) -> tuple[float, float, float]:
    config = _get_scoring_config()
    topic_tokens = _tokenize(topic)
    if not topic_tokens:
        return 0.0, 0.0, 0.0

    candidate_tokens = _tokenize(f"{title} {snippet}")
    overlap_ratio = len(topic_tokens.intersection(candidate_tokens)) / max(len(topic_tokens), 1)

    try:
        parsed_score = float(exa_score)
    except (TypeError, ValueError):
        parsed_score = 0.0

    normalized_exa_score = max(0.0, min(parsed_score, 1.0))

    if normalized_exa_score < float(config["MIN_EXA_SCORE"]):
        return 0.0, overlap_ratio, normalized_exa_score

    topic_strength = (0.7 * overlap_ratio) + (0.3 * normalized_exa_score)
    return max(0.0, min(topic_strength, 1.0)), overlap_ratio, normalized_exa_score


def _infer_page_content_type(project_page) -> str:
    seed = " ".join(
        [
            getattr(project_page, "type_ai_guess", "") or "",
            getattr(project_page, "title", "") or "",
            getattr(project_page, "url", "") or "",
        ]
    ).lower()

    if any(token in seed for token in ("doc", "api", "reference", "developer")):
        return "documentation"
    if any(
        token in seed
        for token in ("product", "feature", "solution", "tool", "software")
    ):
        return "product"
    if any(token in seed for token in ("landing", "pricing", "plan", "overview", "demo")):
        return "landing"
    return "blog"


def _compute_content_type_fit(
    *, expected_type: str, normalized_url: str, title: str, snippet: str
) -> float:
    haystack = f"{normalized_url} {title} {snippet}".lower()
    expected_hints = _CONTENT_TYPE_HINTS.get(expected_type, _CONTENT_TYPE_HINTS["blog"])

    match_count = sum(1 for hint in expected_hints if hint in haystack)
    if match_count == 0:
        return 0.25

    return min(1.0, 0.35 + (0.22 * match_count))


def _compute_domain_credibility(*, domain: str) -> float:
    canonical = _registrable_domain(domain)
    if not canonical:
        return 0.0

    score = 0.45

    if canonical.endswith(".gov") or canonical.endswith(".edu") or canonical.endswith(".org"):
        score += 0.25
    if canonical in _CREDIBILITY_BONUS_DOMAINS or domain in _CREDIBILITY_BONUS_DOMAINS:
        score = max(
            score,
            _CREDIBILITY_BONUS_DOMAINS.get(canonical, 0.0),
            _CREDIBILITY_BONUS_DOMAINS.get(domain, 0.0),
        )
    if len(canonical.split(".")) == 2:
        score += 0.1

    subdomain_prefix = (domain or "").split(".")[0]
    if subdomain_prefix in _CREDIBILITY_LOW_SIGNAL_SUBDOMAIN_PREFIXES:
        score -= 0.1

    return max(0.0, min(score, 1.0))


def _parse_candidate_datetime(candidate: dict) -> datetime | None:
    keys = ("publishedDate", "published_date", "publishedAt", "published_at")
    for key in keys:
        raw = candidate.get(key)
        if not raw:
            continue

        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)

        raw_str = str(raw).strip()
        if not raw_str:
            continue

        try:
            parsed = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _compute_freshness_signal(candidate: dict) -> float:
    published_at = _parse_candidate_datetime(candidate)
    if not published_at:
        return 0.5

    now = datetime.now(tz=timezone.utc)
    age_days = max((now - published_at.astimezone(timezone.utc)).days, 0)

    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.85
    if age_days <= 180:
        return 0.7
    if age_days <= 365:
        return 0.55
    if age_days <= 730:
        return 0.35
    return 0.2


def _score_candidate(*, topic: str, candidate: dict, project_page) -> dict:
    config = _get_scoring_config()
    weights = config["SCORING_WEIGHTS"]

    title = _normalize_phrase((candidate.get("title") or "").strip())
    highlights = candidate.get("highlights") or []
    snippet = " ".join(highlights) if isinstance(highlights, list) else str(highlights)
    snippet = _normalize_phrase(snippet)

    normalized_url = _normalize_candidate_url((candidate.get("url") or "").strip())
    parsed = urlparse(normalized_url) if normalized_url else None
    domain = (parsed.hostname or "").lower() if parsed else ""
    canonical_domain = _registrable_domain(domain)

    if not normalized_url or not domain:
        return {"allowed": False, "reason": "invalid_url"}

    if _is_blocked_domain(domain):
        return {"allowed": False, "reason": "blocked_domain"}

    if _is_low_signal_page(normalized_url=normalized_url, title=title, snippet=snippet):
        return {"allowed": False, "reason": "low_signal_page"}

    topic_match_strength, overlap_ratio, normalized_exa_score = _compute_topic_match_strength(
        topic=topic,
        title=title,
        snippet=snippet,
        exa_score=candidate.get("score"),
    )

    if overlap_ratio < float(config["MIN_TOPIC_OVERLAP_RATIO"]):
        return {"allowed": False, "reason": "low_topic_overlap"}

    expected_content_type = _infer_page_content_type(project_page)
    content_type_fit = _compute_content_type_fit(
        expected_type=expected_content_type,
        normalized_url=normalized_url,
        title=title,
        snippet=snippet,
    )
    domain_credibility = _compute_domain_credibility(domain=domain)
    freshness = _compute_freshness_signal(candidate)

    relevance_score = (
        float(weights["topic_match"]) * topic_match_strength
        + float(weights["content_type_fit"]) * content_type_fit
        + float(weights["domain_credibility"]) * domain_credibility
        + float(weights["freshness"]) * freshness
    )

    if relevance_score < float(config["MIN_RELEVANCE_SCORE"]):
        return {"allowed": False, "reason": "low_relevance_score"}

    score_breakdown = {
        "topic_match_strength": round(topic_match_strength, 4),
        "content_type_fit": round(content_type_fit, 4),
        "domain_credibility": round(domain_credibility, 4),
        "freshness_signal": round(freshness, 4),
        "topic_overlap_ratio": round(overlap_ratio, 4),
        "exa_score": round(normalized_exa_score, 4),
    }

    explanation = {
        "summary": (
            f"Topical match {score_breakdown['topic_match_strength']:.2f}, "
            f"content fit {score_breakdown['content_type_fit']:.2f}, "
            f"domain credibility {score_breakdown['domain_credibility']:.2f}, "
            f"freshness {score_breakdown['freshness_signal']:.2f}."
        ),
        "topic": topic,
        "expected_content_type": expected_content_type,
    }

    return {
        "allowed": True,
        "normalized_url": normalized_url,
        "domain": domain,
        "canonical_domain": canonical_domain,
        "title": title or domain,
        "snippet": snippet,
        "topic": topic,
        "source": "exa",
        "relevance_score": round(relevance_score, 4),
        "score_breakdown": score_breakdown,
        "explanation": explanation,
    }


def _search_exa_for_topic(*, exa_api_key: str, topic: str, num_results: int = 6) -> list[dict]:
    response = requests.post(
        "https://api.exa.ai/search",
        headers={
            "x-api-key": exa_api_key,
            "Content-Type": "application/json",
        },
        json={
            "query": f"{topic} best practices guide",
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


def discover_backlink_prospects(
    project_page, max_candidates: int = 8, max_topics: int = 5
) -> list[dict]:
    """Discover external backlink prospects for a project page via Exa search."""
    config = _get_scoring_config()
    max_candidates = max(1, min(int(config["MAX_CANDIDATES"]), int(max_candidates or 8)))
    exa_api_key = (getattr(settings, "EXA_API_KEY", "") or "").strip()

    if not exa_api_key:
        return []

    project = project_page.project
    topics = extract_backlink_topics(project, project_page, max_topics=max_topics)
    if not topics:
        return []

    project_domain = _registrable_domain(urlparse(getattr(project, "url", "")).hostname or "")

    candidates: list[dict] = []
    seen_dedup_keys: set[tuple[str, str]] = set()

    try:
        for topic in topics:
            results = _search_exa_for_topic(
                exa_api_key=exa_api_key,
                topic=topic,
                num_results=max(6, max_candidates),
            )

            for item in results:
                scored = _score_candidate(
                    topic=topic,
                    candidate=item,
                    project_page=project_page,
                )
                if not scored.get("allowed"):
                    continue

                canonical_domain = scored.get("canonical_domain", "")
                if project_domain and canonical_domain == project_domain:
                    continue

                dedup_key = (canonical_domain, scored["normalized_url"])
                if dedup_key in seen_dedup_keys:
                    continue

                candidate_payload = {
                    "url": scored["normalized_url"],
                    "domain": scored["domain"],
                    "canonical_domain": canonical_domain,
                    "title": scored["title"],
                    "snippet": scored["snippet"],
                    "topic": scored["topic"],
                    "source": scored["source"],
                    "relevance_score": scored["relevance_score"],
                    "score_breakdown": scored["score_breakdown"],
                    "explanation": scored["explanation"],
                }

                candidates.append(candidate_payload)
                seen_dedup_keys.add(dedup_key)

        candidates.sort(key=lambda candidate: candidate.get("relevance_score", 0.0), reverse=True)
        return candidates[:max_candidates]

    except requests.RequestException as error:
        logger.warning(
            "[BacklinkProspects] Exa lookup failed",
            error=str(error),
            project_id=getattr(project, "id", None),
            project_page_id=getattr(project_page, "id", None),
            exc_info=True,
        )
        return []


def refresh_backlink_prospects_cache(project_page) -> list[dict]:
    try:
        candidates = discover_backlink_prospects(project_page)
        set_cached_backlink_prospects(project_page.id, candidates)
        return candidates
    finally:
        cache.delete(get_backlink_prospects_refresh_lock_key(project_page.id))
