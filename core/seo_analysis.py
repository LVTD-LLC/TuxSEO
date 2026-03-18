import re
from typing import Any
from urllib.parse import urlparse

from core.models import ProjectPage

_TITLE_MIN_LENGTH = 30
_TITLE_MAX_LENGTH = 60
_DESCRIPTION_MIN_LENGTH = 120
_DESCRIPTION_MAX_LENGTH = 160
_MIN_BODY_WORD_COUNT = 250
_MIN_INTERNAL_LINKS = 2
_MIN_SUMMARY_WORD_COUNT = 20

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_H1_RE = re.compile(r"^\s*#\s+\S", re.MULTILINE)


def analyze_project_page_seo(project_page: ProjectPage) -> dict[str, Any]:
    markdown_content = project_page.markdown_content or ""
    title = (project_page.title or "").strip()
    description = (project_page.description or "").strip()
    summary = (project_page.summary or "").strip()

    title_length = len(title)
    description_length = len(description)
    summary_word_count = _word_count(summary)
    body_word_count = _word_count(_strip_markdown(markdown_content))
    h1_count = len(_H1_RE.findall(markdown_content))
    internal_link_count = _count_internal_links(markdown_content, project_page.url)

    checks = [
        _check_item(
            key="title_length",
            label="Title length",
            passed=_TITLE_MIN_LENGTH <= title_length <= _TITLE_MAX_LENGTH,
            value=f"{title_length} chars",
            recommendation=f"Keep title between {_TITLE_MIN_LENGTH}-{_TITLE_MAX_LENGTH} characters.",
        ),
        _check_item(
            key="meta_description_length",
            label="Meta description length",
            passed=_DESCRIPTION_MIN_LENGTH <= description_length <= _DESCRIPTION_MAX_LENGTH,
            value=f"{description_length} chars",
            recommendation=(
                f"Keep description between {_DESCRIPTION_MIN_LENGTH}-{_DESCRIPTION_MAX_LENGTH} characters."
            ),
        ),
        _check_item(
            key="h1_presence",
            label="H1 heading",
            passed=h1_count >= 1,
            value=f"{h1_count} found",
            recommendation="Add a single clear H1 heading near the top of the page.",
        ),
        _check_item(
            key="body_word_count",
            label="Body content depth",
            passed=body_word_count >= _MIN_BODY_WORD_COUNT,
            value=f"{body_word_count} words",
            recommendation=f"Expand body copy to at least {_MIN_BODY_WORD_COUNT} words.",
        ),
        _check_item(
            key="internal_links",
            label="Internal links",
            passed=internal_link_count >= _MIN_INTERNAL_LINKS,
            value=f"{internal_link_count} links",
            recommendation=(
                f"Add at least {_MIN_INTERNAL_LINKS} relevant internal links to strengthen crawl paths."
            ),
        ),
        _check_item(
            key="summary_quality",
            label="Summary coverage",
            passed=summary_word_count >= _MIN_SUMMARY_WORD_COUNT,
            value=f"{summary_word_count} words",
            recommendation="Provide a richer summary with key intent and page purpose.",
        ),
    ]

    passed_checks = sum(1 for check in checks if check["passed"])
    total_checks = len(checks)
    score = round((passed_checks / total_checks) * 100) if total_checks else 0

    return {
        "score": score,
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "checks": checks,
        "issues": [check["label"] for check in checks if not check["passed"]],
    }


def _check_item(
    *,
    key: str,
    label: str,
    passed: bool,
    value: str,
    recommendation: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "passed": passed,
        "status": "pass" if passed else "fail",
        "value": value,
        "recommendation": recommendation,
    }


def _word_count(value: str) -> int:
    if not value:
        return 0
    return len(_WORD_RE.findall(value))


def _strip_markdown(content: str) -> str:
    if not content:
        return ""
    without_code = re.sub(r"`[^`]*`", " ", content)
    without_links = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", without_code)
    return re.sub(r"[#>*_\-]", " ", without_links)


def _count_internal_links(markdown_content: str, page_url: str) -> int:
    if not markdown_content:
        return 0

    host = urlparse(page_url).netloc.lower()
    links: set[str] = set()
    for raw_target in _MARKDOWN_LINK_RE.findall(markdown_content):
        target = raw_target.strip()
        if not target or target.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        parsed_target = urlparse(target)
        if target.startswith("/") or (not parsed_target.scheme and not parsed_target.netloc):
            links.add(target)
            continue

        if parsed_target.netloc.lower() == host:
            links.add(target)

    return len(links)
