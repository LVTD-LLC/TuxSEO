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
    assert result["total_checks"] == 6
    assert result["issues"] == ["Meta description length"]


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
