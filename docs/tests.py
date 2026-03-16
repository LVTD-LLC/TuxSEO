import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory, override_settings

from docs.views import (
    docs_page_view,
    get_docs_navigation,
    get_flat_page_list,
    get_previous_and_next_pages,
    load_navigation_config,
)


@pytest.fixture
def request_factory():
    return RequestFactory()


def create_markdown_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _get_docs_markdown_files() -> list[Path]:
    docs_content_root = Path(__file__).resolve().parent / "content"
    return sorted(docs_content_root.rglob("*.md"))


@override_settings(BASE_DIR="/tmp/nonexistent-base-dir")
def test_load_navigation_config_returns_empty_dict_when_file_is_missing():
    assert load_navigation_config() == {}


def test_load_navigation_config_reads_navigation_yaml(tmp_path):
    navigation_file = tmp_path / "docs" / "navigation.yaml"
    navigation_file.parent.mkdir(parents=True, exist_ok=True)
    navigation_file.write_text(
        """
navigation:
  getting-started:
    - introduction
  features:
    - blog-post-suggestions
""",
        encoding="utf-8",
    )

    with override_settings(BASE_DIR=tmp_path):
        navigation_config = load_navigation_config()

    assert navigation_config == {
        "getting-started": ["introduction"],
        "features": ["blog-post-suggestions"],
    }


def test_get_docs_navigation_respects_custom_order_and_includes_remaining_items(tmp_path):
    create_markdown_file(
        tmp_path / "docs" / "content" / "getting-started" / "quickstart.md",
        "# Quickstart",
    )
    create_markdown_file(
        tmp_path / "docs" / "content" / "getting-started" / "introduction.md",
        "# Introduction",
    )
    create_markdown_file(
        tmp_path / "docs" / "content" / "features" / "blog-post-suggestions.md",
        "# Features",
    )

    navigation_file = tmp_path / "docs" / "navigation.yaml"
    navigation_file.parent.mkdir(parents=True, exist_ok=True)
    navigation_file.write_text(
        """
navigation:
  features:
    - blog-post-suggestions
  getting-started:
    - introduction
""",
        encoding="utf-8",
    )

    with override_settings(BASE_DIR=tmp_path):
        navigation = get_docs_navigation()

    assert [item["category_slug"] for item in navigation] == ["features", "getting-started"]

    getting_started_pages = navigation[1]["pages"]
    assert [page["slug"] for page in getting_started_pages] == ["introduction", "quickstart"]


def test_docs_navigation_groups_api_as_top_level_section():
    navigation = get_docs_navigation()

    assert [item["category_slug"] for item in navigation] == [
        "getting-started",
        "features",
        "deployment",
        "api",
    ]


def test_docs_navigation_places_resource_api_pages_in_api_section():
    navigation = get_docs_navigation()
    pages_by_category = {section["category_slug"]: section["pages"] for section in navigation}

    assert "api" in pages_by_category
    assert [page["slug"] for page in pages_by_category["api"]] == [
        "authorization",
        "account",
        "projects",
        "execution-jobs",
        "title-suggestions",
        "keywords",
        "competitors",
        "project-pages",
        "blog-posts",
    ]
    assert "public-api-architecture" not in {
        page["slug"] for page in pages_by_category.get("api", [])
    }


def test_get_flat_page_list_flattens_navigation_structure():
    navigation = [
        {
            "category": "Getting Started",
            "category_slug": "getting-started",
            "pages": [
                {
                    "slug": "introduction",
                    "title": "Introduction",
                    "url": "/docs/getting-started/introduction/",
                },
                {
                    "slug": "quickstart",
                    "title": "Quickstart",
                    "url": "/docs/getting-started/quickstart/",
                },
            ],
        }
    ]

    flat_pages = get_flat_page_list(navigation)

    assert len(flat_pages) == 2
    assert flat_pages[0]["page_slug"] == "introduction"
    assert flat_pages[1]["page_slug"] == "quickstart"


def test_get_previous_and_next_pages_returns_neighbor_pages():
    navigation = [
        {
            "category": "Getting Started",
            "category_slug": "getting-started",
            "pages": [
                {
                    "slug": "introduction",
                    "title": "Introduction",
                    "url": "/docs/getting-started/introduction/",
                },
                {
                    "slug": "quickstart",
                    "title": "Quickstart",
                    "url": "/docs/getting-started/quickstart/",
                },
            ],
        },
        {
            "category": "Features",
            "category_slug": "features",
            "pages": [
                {
                    "slug": "blog-post-suggestions",
                    "title": "Blog Post Suggestions",
                    "url": "/docs/features/blog-post-suggestions/",
                }
            ],
        },
    ]

    previous_page, next_page = get_previous_and_next_pages(
        navigation=navigation,
        current_category="getting-started",
        current_page="quickstart",
    )

    assert previous_page["page_slug"] == "introduction"
    assert next_page["page_slug"] == "blog-post-suggestions"


def test_docs_page_view_loads_markdown_and_builds_context(tmp_path, request_factory):
    create_markdown_file(
        tmp_path / "docs" / "content" / "getting-started" / "introduction.md",
        """---
title: Intro Page
description: Intro description
keywords: intro,getting-started
author: Greg
canonical_url: https://docs.example.com/getting-started/introduction
---
# Welcome

This is the introduction page.
""",
    )

    navigation_file = tmp_path / "docs" / "navigation.yaml"
    navigation_file.parent.mkdir(parents=True, exist_ok=True)
    navigation_file.write_text(
        """
navigation:
  getting-started:
    - introduction
""",
        encoding="utf-8",
    )

    request = request_factory.get("/docs/getting-started/introduction/")

    captured_context = {}

    def fake_render(_request, _template, context):
        captured_context.update(context)
        return HttpResponse("ok")

    with override_settings(BASE_DIR=tmp_path), patch("docs.views.render", side_effect=fake_render):
        response = docs_page_view(request, category="getting-started", page="introduction")

    assert response.status_code == 200
    assert captured_context["page_title"] == "Intro Page"
    assert captured_context["meta_description"] == "Intro description"
    assert "<h1>Welcome</h1>" in captured_context["content"]


def test_docs_page_view_raises_404_for_missing_file(tmp_path, request_factory):
    request = request_factory.get("/docs/missing/page/")

    with override_settings(BASE_DIR=tmp_path), pytest.raises(Http404) as exc_info:
        docs_page_view(request, category="missing", page="page")

    assert "Documentation page not found" in str(exc_info.value)


def test_api_openapi_json_is_served_from_canonical_path(client):
    response = client.get("/api/openapi.json", follow=False)

    assert response.status_code == 200
    content = response.json()
    assert "/public-api/account" in content["paths"]


def test_legacy_public_api_docs_redirects_to_canonical_api_docs(client):
    response = client.get("/public-api/docs", follow=False)

    assert response.status_code == 302
    assert response["Location"] == "/api/docs"


def test_docs_root_redirects_to_docs_introduction(client):
    response = client.get("/docs", follow=False)

    assert response.status_code == 302
    assert response["Location"] == "/docs/getting-started/introduction/"


def test_docs_root_with_trailing_slash_redirects_to_docs_introduction(client):
    response = client.get("/docs/", follow=False)

    assert response.status_code == 302
    assert response["Location"] == "/docs/getting-started/introduction/"


@pytest.mark.parametrize(
    ("slug", "expected_endpoints"),
    [
        ("authorization", ["X-API-Key", "GET /api/docs", "GET /api/openapi.json"]),
        ("account", ["GET /public-api/account"]),
        (
            "projects",
            [
                "GET /public-api/projects",
                "POST /public-api/projects",
                "GET /public-api/projects/{project_id}",
                "PATCH /public-api/projects/{project_id}",
            ],
        ),
        (
            "execution-jobs",
            [
                "POST /public-api/projects/{project_id}/executions",
                "GET /public-api/executions",
                "GET /public-api/executions/{job_id}",
                "POST /public-api/executions/{job_id}/cancel",
                "POST /public-api/executions/{job_id}/retry",
            ],
        ),
        (
            "title-suggestions",
            [
                "GET /public-api/projects/{project_id}/title-suggestions",
                "GET /public-api/projects/{project_id}/title-suggestions/{suggestion_id}",
                "POST /public-api/projects/{project_id}/title-suggestions",
            ],
        ),
        (
            "keywords",
            [
                "GET /public-api/projects/{project_id}/keywords",
                "GET /public-api/projects/{project_id}/keywords/{keyword_id}",
                "POST /public-api/projects/{project_id}/keywords",
            ],
        ),
        (
            "competitors",
            [
                "GET /public-api/projects/{project_id}/competitors",
                "GET /public-api/projects/{project_id}/competitors/{competitor_id}",
                "POST /public-api/projects/{project_id}/competitors",
            ],
        ),
        (
            "project-pages",
            [
                "GET /public-api/projects/{project_id}/pages",
                "GET /public-api/projects/{project_id}/pages/{page_id}",
                "POST /public-api/projects/{project_id}/pages",
            ],
        ),
        (
            "blog-posts",
            [
                "POST /public-api/projects/{project_id}/blog-posts/generate",
                "GET /public-api/projects/{project_id}/blog-posts",
                "GET /public-api/projects/{project_id}/blog-posts/{blog_post_id}",
                "POST /public-api/projects/{project_id}/blog-posts/{blog_post_id}/publish",
            ],
        ),
    ],
)
def test_api_docs_pages_include_expected_endpoints(slug, expected_endpoints):
    docs_root = Path(__file__).resolve().parent / "content"
    api_doc = docs_root / "api" / f"{slug}.md"
    content = api_doc.read_text(encoding="utf-8")

    for endpoint in expected_endpoints:
        assert endpoint in content


def test_legacy_api_docs_page_redirects_to_docs_page(client):
    response = client.get("/api/docs/getting-started/introduction/", follow=False)

    assert response.status_code == 302
    assert response["Location"] == "/docs/getting-started/introduction/"


def test_docs_category_page_is_served_from_existing_docs_content(client):
    with patch("docs.views.render", return_value=HttpResponse("ok")):
        response = client.get("/docs/getting-started/introduction/")

    assert response.status_code == 200


def test_docs_markdown_internal_links_use_docs_paths():
    markdown_link_pattern = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)]+)\)")
    docs_content_root = Path(__file__).resolve().parent / "content"
    validation_errors = []

    for markdown_file in _get_docs_markdown_files():
        markdown_content = markdown_file.read_text(encoding="utf-8")
        for raw_target in markdown_link_pattern.findall(markdown_content):
            link_target = raw_target.strip().split(maxsplit=1)[0]
            if not link_target or link_target.startswith(("http://", "https://", "#", "mailto:")):
                continue

            if link_target.startswith("/api/docs/"):
                validation_errors.append(
                    f"{markdown_file}: use '/docs/' paths instead of '{link_target}'"
                )
                continue

            if not link_target.startswith("/docs/"):
                continue

            path_without_prefix = link_target.removeprefix("/docs/").strip("/")
            path_parts = path_without_prefix.split("/")
            if len(path_parts) != 2:
                validation_errors.append(
                    f"{markdown_file}: invalid docs path format '{link_target}'"
                )
                continue

            category_slug, page_slug = path_parts
            target_markdown_file = docs_content_root / category_slug / f"{page_slug}.md"
            if not target_markdown_file.exists():
                validation_errors.append(
                    f"{markdown_file}: docs link target not found for '{link_target}'"
                )

    assert not validation_errors, "\n".join(validation_errors)


def test_docs_markdown_curl_commands_use_expected_format():
    curl_command_pattern = re.compile(
        r'^curl -o \S+ https://\S+$|^curl -X [A-Z]+ "https://[^"]+"(?: \\)?$'
    )
    validation_errors = []

    for markdown_file in _get_docs_markdown_files():
        markdown_content = markdown_file.read_text(encoding="utf-8")
        current_fence_language = None

        for line_number, line in enumerate(markdown_content.splitlines(), start=1):
            stripped_line = line.strip()

            if stripped_line.startswith("```"):
                if current_fence_language is None:
                    current_fence_language = stripped_line[3:].strip().lower()
                else:
                    current_fence_language = None
                continue

            if not stripped_line.startswith("curl "):
                continue

            if current_fence_language != "bash":
                validation_errors.append(
                    f"{markdown_file}:{line_number} curl command must be in a bash code block"
                )
                continue

            if not curl_command_pattern.match(stripped_line):
                validation_errors.append(
                    f"{markdown_file}:{line_number} curl command has unexpected format"
                )

    assert not validation_errors, "\n".join(validation_errors)
