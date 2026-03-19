import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
    "OVERCOLLECT_FACTOR": 3,
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

_LOW_SIGNAL_PATH_SEGMENTS = (
    "/tag",
    "/tags",
    "/category",
    "/categories",
    "/author",
    "/authors",
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

_QUERY_STRIP_PREFIXES = ("utm_",)
_QUERY_STRIP_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}

_CONTACT_METHOD_ORDER = (
    "contact_page_url",
    "public_email",
    "x_twitter",
    "linkedin",
    "author_profile",
)

_CONTACT_METHOD_LABELS = {
    "contact_page_url": "Contact page",
    "public_email": "Public email",
    "x_twitter": "X/Twitter",
    "linkedin": "LinkedIn",
    "author_profile": "Author profile",
}

_EMAIL_REGEX = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SOCIAL_SKIP_PATH_PREFIXES = (
    "share",
    "intent",
    "home",
    "search",
    "hashtag",
    "status",
    "i/",
)

_CONTACT_HINT_PATTERNS = (
    r"\bcontact\b",
    r"\bget\s+in\s+touch\b",
    r"\breach\s+out\b",
    r"\breach\s+us\b",
    r"\bsupport\b",
)
_LOW_CONTACT_HINT_PATTERNS = (
    r"\babout\b",
    r"\bteam\b",
    r"\bcompany\b",
)
_AUTHOR_HINT_PATTERNS = (
    r"\bauthor\b",
    r"\bprofile\b",
    r"\babout\s+the\s+author\b",
)


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
        if key_lower in _QUERY_STRIP_EXACT:
            continue
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


def _extract_anchor_hrefs(html: str) -> list[tuple[str, str]]:
    hrefs: list[tuple[str, str]] = []
    if not html:
        return hrefs

    for match in re.finditer(
        r"<a\b[^>]*href\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape((match.group(2) or "").strip())
        inner_html = match.group(3) or ""
        text = re.sub(r"<[^>]+>", " ", inner_html)
        text = _normalize_phrase(unescape(text))
        if href:
            hrefs.append((href, text))

    return hrefs


def _init_contact_method(method_type: str, candidate_url: str) -> dict:
    return {
        "type": method_type,
        "label": _CONTACT_METHOD_LABELS.get(method_type, method_type),
        "status": "not_found",
        "confidence": "none",
        "value": "",
        "source_trace": {
            "source_url": candidate_url,
            "signal": "none",
            "evidence": "No reliable public signal detected.",
        },
    }


def _set_contact_method(
    methods_by_type: dict[str, dict],
    *,
    method_type: str,
    status: str,
    confidence: str,
    value: str,
    signal: str,
    evidence: str,
    source_url: str,
) -> None:
    methods_by_type[method_type] = {
        "type": method_type,
        "label": _CONTACT_METHOD_LABELS.get(method_type, method_type),
        "status": status,
        "confidence": confidence,
        "value": value or "",
        "source_trace": {
            "source_url": source_url,
            "signal": signal,
            "evidence": _normalize_phrase(evidence) or "Public signal extracted from page HTML.",
        },
    }


def _normalize_social_profile_url(url: str, *, kind: str) -> str:
    normalized = _normalize_candidate_url(url)
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").strip("/")
    if not path:
        return ""

    path_lower = path.lower()
    if any(path_lower.startswith(prefix) for prefix in _SOCIAL_SKIP_PATH_PREFIXES):
        return ""

    if kind == "x_twitter":
        if not (
            host in {"x.com", "twitter.com"}
            or host.endswith(".x.com")
            or host.endswith(".twitter.com")
        ):
            return ""

        username = path.split("/")[0]
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
            return ""

        return f"https://x.com/{username}"

    if kind == "linkedin":
        if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            return ""

        top_segment = path.split("/")[0].lower()
        if top_segment not in {"company", "in", "school"}:
            return ""

        return f"https://www.linkedin.com/{path}"

    return ""


def _matches_any_pattern(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value) for pattern in patterns)


def _is_author_profile_signal(*, absolute_url: str, label_lower: str) -> bool:
    parsed = urlparse(absolute_url)
    path_lower = (parsed.path or "").lower()

    if any(token in path_lower for token in ("/author", "/authors", "/profile", "/profiles")):
        return True

    combined = f"{absolute_url} {label_lower}"
    return _matches_any_pattern(combined, _AUTHOR_HINT_PATTERNS)


def _extract_public_contact_methods(*, candidate_url: str, html: str) -> tuple[list[dict], list[dict]]:
    """Extract outreach contact signals from public HTML only.

    Ethical boundary: we only parse publicly visible page HTML and outgoing links.
    We do not query private databases, gated APIs, or infer/fabricate personal data.

    Confidence labels (v1):
    - found/high: explicit and well-formed signal (mailto, social profile URL pattern, contact page).
    - low_confidence/low: weak signal that may be relevant but ambiguous.
    - not_found/none: no signal detected.
    """

    methods_by_type = {
        method_type: _init_contact_method(method_type, candidate_url)
        for method_type in _CONTACT_METHOD_ORDER
    }
    anchors = _extract_anchor_hrefs(html)
    raw_text = _normalize_phrase(unescape(re.sub(r"<[^>]+>", " ", html or "")))

    for href, anchor_text in anchors:
        href_lower = href.lower().strip()
        label_lower = (anchor_text or "").lower()
        absolute_url = _normalize_candidate_url(urljoin(candidate_url, href))

        if href_lower.startswith("mailto:") and methods_by_type["public_email"]["status"] != "found":
            email = href.split(":", 1)[1].split("?", 1)[0].strip()
            if _EMAIL_REGEX.fullmatch(email):
                _set_contact_method(
                    methods_by_type,
                    method_type="public_email",
                    status="found",
                    confidence="high",
                    value=email,
                    signal="mailto",
                    evidence=f"Found mailto link in anchor '{anchor_text or 'email'}'.",
                    source_url=candidate_url,
                )

        if absolute_url:
            contact_signal_text = f"{absolute_url} {label_lower}"

            if methods_by_type["contact_page_url"]["status"] != "found" and _matches_any_pattern(
                contact_signal_text,
                _CONTACT_HINT_PATTERNS,
            ):
                _set_contact_method(
                    methods_by_type,
                    method_type="contact_page_url",
                    status="found",
                    confidence="high",
                    value=absolute_url,
                    signal="contact_link",
                    evidence=f"Anchor text '{anchor_text or absolute_url}' links to contact-related URL.",
                    source_url=candidate_url,
                )

            if methods_by_type["contact_page_url"]["status"] == "not_found" and _matches_any_pattern(
                contact_signal_text,
                _LOW_CONTACT_HINT_PATTERNS,
            ):
                _set_contact_method(
                    methods_by_type,
                    method_type="contact_page_url",
                    status="low_confidence",
                    confidence="low",
                    value=absolute_url,
                    signal="weak_contact_link",
                    evidence=(
                        f"Anchor '{anchor_text or absolute_url}' may help outreach but is not an explicit contact page."
                    ),
                    source_url=candidate_url,
                )

            twitter_profile = _normalize_social_profile_url(absolute_url, kind="x_twitter")
            if twitter_profile and methods_by_type["x_twitter"]["status"] != "found":
                _set_contact_method(
                    methods_by_type,
                    method_type="x_twitter",
                    status="found",
                    confidence="high",
                    value=twitter_profile,
                    signal="social_link",
                    evidence=f"Found profile link in anchor '{anchor_text or twitter_profile}'.",
                    source_url=candidate_url,
                )

            linkedin_profile = _normalize_social_profile_url(absolute_url, kind="linkedin")
            if linkedin_profile and methods_by_type["linkedin"]["status"] != "found":
                _set_contact_method(
                    methods_by_type,
                    method_type="linkedin",
                    status="found",
                    confidence="high",
                    value=linkedin_profile,
                    signal="social_link",
                    evidence=f"Found profile link in anchor '{anchor_text or linkedin_profile}'.",
                    source_url=candidate_url,
                )

            if _is_author_profile_signal(
                absolute_url=absolute_url,
                label_lower=label_lower,
            ) and methods_by_type["author_profile"]["status"] != "found":
                _set_contact_method(
                    methods_by_type,
                    method_type="author_profile",
                    status="found",
                    confidence="medium",
                    value=absolute_url,
                    signal="author_link",
                    evidence=f"Author-style link '{anchor_text or absolute_url}' found on page.",
                    source_url=candidate_url,
                )

    if methods_by_type["public_email"]["status"] == "not_found":
        email_match = _EMAIL_REGEX.search(raw_text or "")
        if email_match:
            _set_contact_method(
                methods_by_type,
                method_type="public_email",
                status="found",
                confidence="medium",
                value=email_match.group(0),
                signal="visible_text",
                evidence="Email pattern detected in visible page text.",
                source_url=candidate_url,
            )

    methods = [methods_by_type[method_type] for method_type in _CONTACT_METHOD_ORDER]
    actionable_paths = [
        method
        for method in methods
        if method["status"] == "found" and method.get("value")
    ]

    return methods, actionable_paths


def _enrich_candidate_contacts(candidate: dict) -> tuple[list[dict], list[dict]]:
    url = (candidate.get("url") or "").strip()
    if not url:
        methods = [_init_contact_method(method_type, "") for method_type in _CONTACT_METHOD_ORDER]
        return methods, []

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "TuxSEO/BacklinkProspectsBot (+https://tuxseo.com)",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=8,
        )
        response.raise_for_status()
        html = response.text or ""
    except requests.RequestException:
        methods = [_init_contact_method(method_type, url) for method_type in _CONTACT_METHOD_ORDER]
        for method in methods:
            method["source_trace"] = {
                "source_url": url,
                "signal": "fetch_failed",
                "evidence": "Could not fetch public page HTML for enrichment.",
            }
        return methods, []

    return _extract_public_contact_methods(candidate_url=url, html=html)


def _path_has_low_signal_segment(path_lower: str) -> bool:
    for segment in _LOW_SIGNAL_PATH_SEGMENTS:
        if path_lower == segment or path_lower.startswith(f"{segment}/"):
            return True
    return False


def _is_low_signal_page(*, normalized_url: str, title: str, snippet: str) -> bool:
    if not normalized_url:
        return True

    parsed = urlparse(normalized_url)
    path_lower = (parsed.path or "").lower()

    if _path_has_low_signal_segment(path_lower):
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
    *, topic: str, title: str, snippet: str, exa_score, config: dict
) -> tuple[float, float, float]:
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
        config=config,
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
    overcollect_limit = max(
        max_candidates,
        max_candidates * int(config.get("OVERCOLLECT_FACTOR", 3)),
    )

    try:
        for topic in topics:
            if len(candidates) >= overcollect_limit:
                break

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
                    "contact_methods": [],
                    "actionable_outreach_paths": [],
                    "actionable_outreach_count": 0,
                }

                candidates.append(candidate_payload)
                seen_dedup_keys.add(dedup_key)

                if len(candidates) >= overcollect_limit:
                    break

        candidates.sort(key=lambda candidate: candidate.get("relevance_score", 0.0), reverse=True)

        selected = candidates[:max_candidates]
        if selected:
            max_workers = max(1, min(4, len(selected)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                enrichment_results = list(executor.map(_enrich_candidate_contacts, selected))

            for candidate, (contact_methods, actionable_paths) in zip(selected, enrichment_results):
                candidate["contact_methods"] = contact_methods
                candidate["actionable_outreach_paths"] = actionable_paths
                candidate["actionable_outreach_count"] = len(actionable_paths)

        return selected

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
