import json
import re
from collections.abc import Mapping
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
_JSON_LD_SCRIPT_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_JSONLD_STATE_OK = "ok"
_JSONLD_STATE_ISSUES = "issues"
_JSONLD_STATE_MISSING = "missing"

_JSONLD_STATE_LABELS = {
    _JSONLD_STATE_OK: "Detected & looks okay",
    _JSONLD_STATE_ISSUES: "Detected but issues",
    _JSONLD_STATE_MISSING: "Missing (suggested starter available)",
}

_COMMON_TYPE_REQUIRED_KEYS = {
    "WebPage": ["name", "url"],
    "Article": ["headline", "author", "datePublished"],
}


def analyze_project_page_seo(project_page: ProjectPage) -> dict[str, Any]:
    markdown_content = project_page.markdown_content or ""
    markdown_without_fenced_code = _strip_fenced_code_blocks(markdown_content)
    title = (project_page.title or "").strip()
    description = (project_page.description or "").strip()
    summary = (project_page.summary or "").strip()

    title_length = len(title)
    description_length = len(description)
    summary_word_count = _word_count(summary)
    body_word_count = _word_count(_strip_markdown(markdown_without_fenced_code))
    h1_count = len(_H1_RE.findall(markdown_without_fenced_code))
    internal_link_count = _count_internal_links(markdown_content, project_page.url)
    json_ld_analysis = analyze_json_ld_schema(
        page_url=project_page.url,
        page_type=project_page.type_ai_guess,
        title=title,
        description=description,
        html_content=markdown_content,
    )

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
        _check_item(
            key="json_ld_schema",
            label="JSON-LD schema",
            passed=json_ld_analysis["state"] == _JSONLD_STATE_OK,
            value=json_ld_analysis["status_label"],
            recommendation="Use valid JSON-LD with @context and @type. Apply the starter suggestion and customize fields.",
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
        "json_ld": json_ld_analysis,
    }


def analyze_json_ld_schema(
    *,
    page_url: str,
    page_type: str,
    title: str,
    description: str,
    html_content: str,
) -> dict[str, Any]:
    script_blocks = _extract_json_ld_script_blocks(html_content)
    items: list[dict[str, Any]] = []
    parse_errors: list[str] = []

    for index, raw_block in enumerate(script_blocks, start=1):
        block_source = (raw_block or "").strip()
        if not block_source:
            parse_errors.append(f"Block {index}: empty JSON-LD script content")
            continue

        try:
            parsed_block = json.loads(block_source)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"Block {index}: malformed JSON ({exc.msg} at line {exc.lineno}, column {exc.colno})")
            continue

        normalized_items = parsed_block if isinstance(parsed_block, list) else [parsed_block]
        for item_position, normalized_item in enumerate(normalized_items, start=1):
            item_issues = _validate_json_ld_item(normalized_item)
            item_type = _extract_schema_type(normalized_item)
            item_context = normalized_item.get("@context") if isinstance(normalized_item, Mapping) else None
            items.append(
                {
                    "block_index": index,
                    "item_index": item_position,
                    "type": item_type,
                    "context": item_context,
                    "issues": item_issues,
                    "is_valid": len(item_issues) == 0,
                }
            )

    has_detected_json_ld = len(script_blocks) > 0
    has_item_issues = any(item["issues"] for item in items)
    has_issues = bool(parse_errors or has_item_issues)

    if not has_detected_json_ld:
        state = _JSONLD_STATE_MISSING
    elif has_issues:
        state = _JSONLD_STATE_ISSUES
    else:
        state = _JSONLD_STATE_OK

    return {
        "state": state,
        "status_label": _JSONLD_STATE_LABELS[state],
        "detected_script_blocks": len(script_blocks),
        "valid_items": sum(1 for item in items if item["is_valid"]),
        "total_items": len(items),
        "parse_errors": parse_errors,
        "items": items,
        "starter_suggestion": (
            build_json_ld_starter_suggestion(
                page_url=page_url,
                page_type=page_type,
                title=title,
                description=description,
            )
            if state in {_JSONLD_STATE_MISSING, _JSONLD_STATE_ISSUES}
            else None
        ),
        "notes": [
            "v1 guidance only: this is a baseline quality check, not strict schema.org compliance validation.",
            "Customize starter values before publishing (author, dates, canonical URL, and publisher details).",
        ],
    }


def build_json_ld_starter_suggestion(
    *,
    page_url: str,
    page_type: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    normalized_page_type = (page_type or "").strip().lower()
    template_kind = "Article" if "blog" in normalized_page_type or "article" in normalized_page_type else "WebPage"

    if template_kind == "Article":
        payload: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title or "Replace with article headline",
            "description": description or "Replace with article summary",
            "author": {
                "@type": "Person",
                "name": "Replace with author name",
            },
            "datePublished": "YYYY-MM-DD",
            "dateModified": "YYYY-MM-DD",
            "mainEntityOfPage": page_url,
        }
    else:
        payload = {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title or "Replace with page title",
            "description": description or "Replace with page description",
            "url": page_url,
        }

    return {
        "template_type": template_kind,
        "json_ld": payload,
        "json_ld_pretty": json.dumps(payload, indent=2, ensure_ascii=False),
        "customization_notes": [
            "Replace placeholder values (especially author and dates).",
            "Keep the schema type aligned with the page's real intent.",
            "Ensure URL fields use canonical public URLs.",
        ],
    }


def _extract_json_ld_script_blocks(html_content: str) -> list[str]:
    if not html_content:
        return []
    return [match.strip() for match in _JSON_LD_SCRIPT_RE.findall(html_content)]


def _validate_json_ld_item(item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return ["JSON-LD item is not an object"]

    issues: list[str] = []

    context = item.get("@context")
    if not context:
        issues.append("Missing @context")
    elif "schema.org" not in str(context):
        issues.append("@context should usually reference schema.org")

    item_type = item.get("@type")
    if not item_type:
        issues.append("Missing @type")

    normalized_type = _extract_schema_type(item)
    required_keys = _COMMON_TYPE_REQUIRED_KEYS.get(normalized_type, [])
    for required_key in required_keys:
        required_value = item.get(required_key)
        if required_value in (None, "", []):
            issues.append(f"Missing required field for {normalized_type}: {required_key}")

    return issues


def _extract_schema_type(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "Unknown"

    item_type = item.get("@type")
    if isinstance(item_type, list):
        for candidate in item_type:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "Unknown"

    if isinstance(item_type, str) and item_type.strip():
        return item_type.strip()

    return "Unknown"


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


def _strip_fenced_code_blocks(content: str) -> str:
    if not content:
        return ""
    return re.sub(r"```.*?```", " ", content, flags=re.DOTALL)


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
