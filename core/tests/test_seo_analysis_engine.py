import pytest
from django.contrib.auth.models import User

from core.models import Project, ProjectPage
from core.seo_analysis import analyze_project_page_seo


@pytest.mark.django_db
def test_analyze_project_page_seo_scores_deterministic_checks():
    user = User.objects.create_user(
        username="seo-analysis-user",
        email="seo-analysis-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    markdown_content = "\n".join(
        [
            "# Product analytics for SaaS teams",
            "Build reliable reporting workflows for growth teams.",
            "[Pricing](/pricing)",
            "[Features](https://example.com/features)",
            " ".join(["growth"] * 260),
        ]
    )

    project_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/blog/analytics-workflows",
        title="Product analytics workflows for SaaS growth teams",
        description="Too short",
        summary="This summary is intentionally long enough to pass by giving a clear explanation of what the page covers and why it exists for buyers.",
        markdown_content=markdown_content,
        type_ai_guess="blog post",
    )

    result = analyze_project_page_seo(project_page)

    assert result["score"] == 83
    assert result["passed_checks"] == 5
    assert result["warned_checks"] == 0
    assert result["failed_checks"] == 1
    assert result["total_checks"] == 6
    assert result["issues"] == ["Meta description length"]
    assert result["json_ld"]["state"] == "missing"
    assert result["json_ld"]["status_label"] == "Missing (suggested starter available)"
    assert result["json_ld"]["is_scorable"] is False


@pytest.mark.django_db
def test_analyze_project_page_seo_ignores_fenced_code_for_h1_and_word_count():
    user = User.objects.create_user(
        username="seo-analysis-code-fence-user",
        email="seo-analysis-code-fence-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    project_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/dev-docs",
        title="Developer docs with snippets and minimal prose",
        description="A" * 130,
        summary="This is a short summary that still provides enough intent words for deterministic summary checks.",
        markdown_content="\n".join(
            [
                "```python",
                "# this is a comment, not a markdown heading",
                "print('hello world')",
                "```",
                "Tiny paragraph.",
            ]
        ),
        type_ai_guess="documentation",
    )

    result = analyze_project_page_seo(project_page)

    h1_check = next(check for check in result["checks"] if check["key"] == "h1_presence")
    body_check = next(check for check in result["checks"] if check["key"] == "body_word_count")

    assert h1_check["passed"] is False
    assert body_check["passed"] is False


@pytest.mark.django_db
def test_analyze_project_page_seo_includes_json_ld_check_when_html_scripts_available():
    user = User.objects.create_user(
        username="seo-analysis-jsonld-user",
        email="seo-analysis-jsonld-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    project_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        title="Feature page title for deterministic SEO validation",
        description="D" * 130,
        summary="This summary has enough words to satisfy deterministic summary quality checks used in tests.",
        markdown_content="\n".join(
            [
                "<script type=\"application/ld+json\">",
                '{"@context":"https://schema.org","@type":"WebPage","name":"Features","url":"https://example.com/features"}',
                "</script>",
                "# Features",
                "[Pricing](/pricing)",
                "[Use cases](https://example.com/use-cases)",
                " ".join(["feature"] * 260),
            ]
        ),
        type_ai_guess="product page",
    )

    result = analyze_project_page_seo(project_page)

    assert result["json_ld"]["is_scorable"] is True
    assert result["json_ld"]["state"] == "ok"
    assert result["total_checks"] == 7
    assert "JSON-LD schema" not in result["issues"]


@pytest.mark.django_db
def test_analyze_project_page_seo_reports_failures_when_page_is_sparse():
    user = User.objects.create_user(
        username="seo-analysis-empty-user",
        email="seo-analysis-empty-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    project_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/empty",
        title="",
        description="",
        summary="",
        markdown_content="",
        type_ai_guess="unknown",
    )

    result = analyze_project_page_seo(project_page)

    assert result["score"] == 0
    assert result["passed_checks"] == 0
    assert result["total_checks"] == 6
    assert len(result["issues"]) == 6
    assert result["json_ld"]["state"] == "missing"
    assert result["json_ld"]["is_scorable"] is False


@pytest.mark.django_db
def test_analyze_project_page_seo_contract_includes_required_fields_and_check_shape():
    user = User.objects.create_user(
        username="seo-analysis-contract-user",
        email="seo-analysis-contract-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    project_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/contract",
        title="Reliable contract test page title for SEO checks",
        description="D" * 140,
        summary="This summary exists to ensure the analyzer returns stable deterministic payload keys for API and UI consumers.",
        markdown_content="\n".join(
            [
                "# Contract Coverage",
                "[Pricing](/pricing)",
                "[Features](https://example.com/features)",
                " ".join(["seo"] * 260),
            ]
        ),
        type_ai_guess="product page",
    )

    result = analyze_project_page_seo(project_page)

    assert set(result.keys()) == {
        "score",
        "passed_checks",
        "warned_checks",
        "failed_checks",
        "total_checks",
        "checks",
        "issues",
        "json_ld",
    }

    assert isinstance(result["score"], int)
    assert isinstance(result["checks"], list)
    assert isinstance(result["issues"], list)

    assert result["checks"], "Expected at least one SEO check in result payload"
    for check in result["checks"]:
        assert {
            "key",
            "label",
            "passed",
            "status",
            "value",
            "why_it_matters",
            "how_to_fix",
            "recommendation",
        }.issubset(check.keys())
        assert check["status"] in {"pass", "warn", "fail"}

    assert {
        "state",
        "status_label",
        "html_input_available",
        "is_scorable",
        "detected_script_blocks",
        "valid_items",
        "total_items",
        "detected_types",
        "detected_summary",
        "parse_errors",
        "issue_list",
        "items",
        "starter_suggestion",
        "notes",
    }.issubset(result["json_ld"].keys())


@pytest.mark.django_db
def test_analyze_project_page_seo_json_ld_states_cover_ok_missing_and_malformed():
    user = User.objects.create_user(
        username="seo-analysis-jsonld-state-user",
        email="seo-analysis-jsonld-state-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )

    page_ok = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page-ok",
        title="Valid JSON-LD SEO test page title",
        description="D" * 140,
        summary="Summary for valid JSON-LD state check.",
        markdown_content="\n".join(
            [
                '<script type="application/ld+json">',
                '{"@context":"https://schema.org","@type":"WebPage","name":"Valid","url":"https://example.com/page-ok"}',
                "</script>",
                "# Heading",
                "[Pricing](/pricing)",
                " ".join(["seo"] * 260),
            ]
        ),
        type_ai_guess="product page",
    )
    result_ok = analyze_project_page_seo(page_ok)
    assert result_ok["json_ld"]["state"] == "ok"

    page_missing = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page-missing",
        title="Missing JSON-LD SEO test page title",
        description="D" * 140,
        summary="Summary for missing JSON-LD state check.",
        markdown_content="\n".join(
            [
                "# Heading",
                "[Pricing](/pricing)",
                " ".join(["seo"] * 260),
            ]
        ),
        type_ai_guess="product page",
    )
    result_missing = analyze_project_page_seo(page_missing)
    assert result_missing["json_ld"]["state"] == "missing"

    page_malformed = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page-malformed",
        title="Malformed JSON-LD SEO test page title",
        description="D" * 140,
        summary="Summary for malformed JSON-LD state check.",
        markdown_content="\n".join(
            [
                '<script type="application/ld+json">',
                '{"@context":"https://schema.org","@type":"WebPage",',
                "</script>",
                "# Heading",
                "[Pricing](/pricing)",
                " ".join(["seo"] * 260),
            ]
        ),
        type_ai_guess="product page",
    )
    result_malformed = analyze_project_page_seo(page_malformed)
    assert result_malformed["json_ld"]["state"] == "issues"
    assert result_malformed["json_ld"]["parse_errors"]
