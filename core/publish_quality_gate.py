import re
from typing import Any

from core.content_quality import evaluate_generated_content_quality

MIN_BLOCKING_WORD_COUNT = 120
MIN_WARNING_WORD_COUNT = 180
MIN_WARNING_AGGREGATE_SCORE = 0.72

PLACEHOLDER_PATTERNS = [
    r"insert\s+(an?\s+)?(image|screenshot|link|video|chart|graphic)\s+(here|below|above)",
    r"(image|screenshot|link)\s+suggestion",
    r"\[(image|screenshot|link|placeholder|todo|tbd)\]",
    r"\b(todo|tbd|to be added|coming soon)\b",
]


def evaluate_pre_publish_quality_gate(generated_post) -> dict[str, Any]:
    content = (generated_post.content or "").strip()
    checks: list[dict[str, str]] = []

    if not content:
        checks.append(
            {
                "severity": "block",
                "code": "CONTENT_EMPTY",
                "message": "Generated content is empty.",
            }
        )
        return _build_gate_result(checks=checks)

    word_count = _count_words(content)
    if word_count < MIN_BLOCKING_WORD_COUNT:
        checks.append(
            {
                "severity": "block",
                "code": "CONTENT_TOO_SHORT",
                "message": (
                    "Generated content is too short for publishing "
                    f"({word_count} words; minimum {MIN_BLOCKING_WORD_COUNT})."
                ),
            }
        )

    if _contains_placeholder_language(content):
        checks.append(
            {
                "severity": "block",
                "code": "PLACEHOLDER_LANGUAGE",
                "message": "Generated content includes placeholder/editorial language.",
            }
        )

    if _has_incomplete_ending(content):
        checks.append(
            {
                "severity": "block",
                "code": "INCOMPLETE_ENDING",
                "message": "Generated content appears cut off before a complete ending.",
            }
        )

    if word_count < MIN_WARNING_WORD_COUNT:
        checks.append(
            {
                "severity": "warn",
                "code": "CONTENT_LENGTH_WARNING",
                "message": (
                    "Generated content is brief "
                    f"({word_count} words; recommended at least {MIN_WARNING_WORD_COUNT})."
                ),
            }
        )

    title = (generated_post.title or "").strip()
    target_keywords = _get_target_keywords(generated_post)
    quality_report = evaluate_generated_content_quality(
        title=title,
        target_keywords=target_keywords,
        generated_content=content,
    )
    aggregate_score = quality_report["aggregate_score"]

    if aggregate_score < MIN_WARNING_AGGREGATE_SCORE:
        checks.append(
            {
                "severity": "warn",
                "code": "LOW_QUALITY_SCORE",
                "message": (
                    "Quality score is below recommendation "
                    f"({aggregate_score:.3f}; recommended >= {MIN_WARNING_AGGREGATE_SCORE:.2f})."
                ),
            }
        )

    return _build_gate_result(checks=checks, aggregate_score=aggregate_score)


def _build_gate_result(
    *,
    checks: list[dict[str, str]],
    aggregate_score: float | None = None,
) -> dict[str, Any]:
    blocking_checks = [check for check in checks if check["severity"] == "block"]
    warning_checks = [check for check in checks if check["severity"] == "warn"]

    if blocking_checks:
        decision = "block"
    elif warning_checks:
        decision = "warn"
    else:
        decision = "allow"

    return {
        "decision": decision,
        "checks": checks,
        "blocking_checks": blocking_checks,
        "warning_checks": warning_checks,
        "summary": "; ".join(check["message"] for check in checks) if checks else "",
        "aggregate_score": aggregate_score,
    }


def _get_target_keywords(generated_post) -> list[str]:
    title_suggestion = getattr(generated_post, "title_suggestion", None)
    if title_suggestion is None:
        return []

    target_keywords = getattr(title_suggestion, "target_keywords", None) or []
    if not isinstance(target_keywords, (list, tuple, set)):
        return []

    return [str(keyword) for keyword in target_keywords if str(keyword).strip()]


def _contains_placeholder_language(blog_post_content: str) -> bool:
    return any(
        re.search(pattern, blog_post_content, re.IGNORECASE) for pattern in PLACEHOLDER_PATTERNS
    )


def _has_incomplete_ending(blog_post_content: str) -> bool:
    normalized_content = blog_post_content.strip()
    if not normalized_content:
        return True

    non_empty_lines = [line.strip() for line in normalized_content.splitlines() if line.strip()]
    if not non_empty_lines:
        return True

    last_line = non_empty_lines[-1]

    if re.search(r"[:;,\-(\[]$", last_line) or last_line.endswith("..."):
        return True

    if re.search(r"\b(and|or|but|because|with|to|for|in|on|at|of|the|a|an)$", last_line.lower()):
        return True

    return re.search(r"[.!?](?:[\"'\)\]]+)?$", last_line) is None


def _count_words(content: str) -> int:
    return len(re.findall(r"[a-z0-9]+", content.lower()))
