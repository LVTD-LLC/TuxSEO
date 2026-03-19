import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import Project, ProjectPage, ProjectPageAnalysisRun
from core.seo_analysis import analyze_project_page_seo


@pytest.mark.django_db
def test_project_pages_list_links_to_page_detail_view(client):
    user = User.objects.create_user(
        username="pages-list-link-user",
        email="pages-list-link-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    client.force_login(user)
    response = client.get(reverse("project_pages", kwargs={"pk": project.id}))

    assert response.status_code == 200
    assert reverse(
        "project_page_detail",
        kwargs={"project_pk": project.id, "page_pk": page.id},
    ) in response.content.decode()


@pytest.mark.django_db
def test_project_page_detail_view_allows_paid_non_admin_users(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-pro-user",
        email="page-detail-pro-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        summary="Feature page summary",
        type_ai_guess="product page",
    )

    monkeypatch.setattr(
        user.profile.__class__,
        "is_on_pro_plan",
        property(lambda _self: True),
    )

    client.force_login(user)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Page Command Center" in content
    assert "Overview" in content
    assert "SEO Analysis" in content
    assert "Backlink Opportunities" in content


@pytest.mark.django_db
def test_project_page_detail_view_blocks_free_users_with_upgrade_cta(client):
    user = User.objects.create_user(
        username="page-detail-free-user",
        email="page-detail-free-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    client.force_login(user)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 403
    assert "This feature is available on Pro" in content
    assert reverse("user_upgrade_checkout_session", kwargs={"product_name": "Pro - Monthly"}) in content


@pytest.mark.django_db
def test_project_page_detail_view_returns_404_for_non_owner(client):
    owner = User.objects.create_user(
        username="page-detail-owner-user",
        email="page-detail-owner-user@example.com",
        password="secret",
    )
    other = User.objects.create_user(
        username="page-detail-other-user",
        email="page-detail-other-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=owner.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    client.force_login(other)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_project_page_detail_view_renders_deterministic_seo_analysis(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-seo-analysis-user",
        email="page-detail-seo-analysis-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        title="SEO command center page title for deterministic checks",
        description="Too short",
        summary="This summary explains intent and page value with enough detail to pass deterministic summary checks.",
        markdown_content="\n".join(
            [
                "# Feature overview",
                "Our platform helps SaaS teams improve content operations.",
                "[Pricing](/pricing)",
                "[Use cases](https://example.com/use-cases)",
                " ".join(["seo"] * 260),
            ]
        ),
        date_analyzed=timezone.now(),
        type_ai_guess="product page",
    )

    monkeypatch.setattr(
        user.profile.__class__,
        "is_on_pro_plan",
        property(lambda _self: True),
    )

    client.force_login(user)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    content = response.content.decode()
    expected_analysis = analyze_project_page_seo(page)

    assert response.status_code == 200
    assert "Overall v1 score:" in content
    assert f"{expected_analysis['score']}/100" in content
    assert "Meta description length" in content
    assert "Why it matters:" in content
    assert "How to fix:" in content
    assert "JSON-LD recommendations" in content
    assert "Detected schema summary:" in content
    assert "Suggested starter block (copy and customize): WebPage" in content


@pytest.mark.django_db
def test_project_page_detail_view_renders_backlink_candidates_for_pro_users(client, monkeypatch):
    user = User.objects.create_superuser(
        username="page-detail-backlink-user",
        email="page-detail-backlink-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        title="Features",
        summary="Feature page summary",
        type_ai_guess="product page",
        date_analyzed=timezone.now(),
    )

    monkeypatch.setattr(
        "core.views.get_cached_backlink_prospects",
        lambda _project_page_id: [
            {
                "url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
                "domain": "developers.google.com",
                "title": "Google SEO Starter Guide",
                "snippet": "Technical SEO indexing best practices",
                "topic": "technical seo",
                "source": "exa",
                "relevance_score": 0.92,
                "contact_methods": [
                    {
                        "type": "contact_page_url",
                        "label": "Contact page",
                        "status": "found",
                        "confidence": "high",
                        "value": "https://developers.google.com/contact",
                        "source_trace": {
                            "evidence": "Anchor text 'Contact us' links to contact-related URL.",
                        },
                    },
                    {
                        "type": "public_email",
                        "label": "Public email",
                        "status": "not_found",
                        "confidence": "none",
                        "value": "",
                        "source_trace": {
                            "evidence": "No reliable public signal detected.",
                        },
                    },
                ],
            }
        ],
    )

    client.force_login(user)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Google SEO Starter Guide" in content
    assert "1 relevant prospects found" in content
    assert "Topic: technical seo" in content
    assert "Outreach signals (public web only)" in content
    assert "Contact page:" in content
    assert "Found · high" in content
    assert "Public email:" in content
    assert "none" in content


@pytest.mark.django_db
def test_project_page_detail_view_supports_explicit_error_state_for_shell(client):
    user = User.objects.create_superuser(
        username="page-detail-state-user",
        email="page-detail-state-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    client.force_login(user)
    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
        + "?state=error"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "We could not load this page overview right now. Please try again." in content
    assert "SEO analysis failed to load. Please retry with “Refresh analysis”." in content
    assert "Could not load backlink opportunities. Please retry." in content


@pytest.mark.django_db
def test_project_page_detail_view_refresh_action_redirects_after_success(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-refresh-success-user",
        email="page-detail-refresh-success-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    scheduled_tasks = []

    def _fake_async_task(*args, **kwargs):
        scheduled_tasks.append((args, kwargs))
        return "task-id"

    monkeypatch.setattr(user.profile.__class__, "is_on_pro_plan", property(lambda _self: True))
    monkeypatch.setattr("core.views.async_task", _fake_async_task)

    client.force_login(user)
    response = client.post(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        ),
        data={"action": "run_seo_analysis"},
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "project_page_detail",
        kwargs={"project_pk": project.id, "page_pk": page.id},
    )
    assert len(scheduled_tasks) == 1
    assert scheduled_tasks[0][0][0] == "core.tasks.execute_project_page_analysis_run"
    assert ProjectPageAnalysisRun.objects.filter(project_page=page).count() == 1


@pytest.mark.django_db
def test_project_page_detail_view_refresh_action_redirects_after_failure(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-refresh-failed-user",
        email="page-detail-refresh-failed-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )
    ProjectPageAnalysisRun.objects.create(
        project=project,
        project_page=page,
        requested_by=user.profile,
        status=ProjectPageAnalysisRun.Status.FAILED,
        finished_at=timezone.now(),
    )

    scheduled_tasks = []

    def _fake_async_task(*args, **kwargs):
        scheduled_tasks.append((args, kwargs))
        return "task-id"

    monkeypatch.setattr(user.profile.__class__, "is_on_pro_plan", property(lambda _self: True))
    monkeypatch.setattr("core.views.async_task", _fake_async_task)

    client.force_login(user)
    response = client.post(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        ),
        data={"action": "run_seo_analysis"},
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "project_page_detail",
        kwargs={"project_pk": project.id, "page_pk": page.id},
    )
    assert len(scheduled_tasks) == 1
    assert ProjectPageAnalysisRun.objects.filter(project_page=page).count() == 2


@pytest.mark.django_db
def test_project_page_detail_view_refresh_action_forbidden_for_free_users(client):
    user = User.objects.create_user(
        username="page-detail-refresh-free-user",
        email="page-detail-refresh-free-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example Project",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    client.force_login(user)
    response = client.post(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        ),
        data={"action": "run_seo_analysis"},
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_project_page_detail_view_shows_failed_run_and_retry_action(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-failed-run-user",
        email="page-detail-failed-run-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        type_ai_guess="product page",
    )

    ProjectPageAnalysisRun.objects.create(
        project=project,
        project_page=page,
        requested_by=user.profile,
        status=ProjectPageAnalysisRun.Status.FAILED,
        failure_message="Failed to fetch page content for SEO analysis.",
    )

    monkeypatch.setattr(
        user.profile.__class__,
        "is_on_pro_plan",
        property(lambda _self: True),
    )
    client.force_login(user)

    response = client.get(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Latest run status: Failed" in content
    assert "Retry analysis" in content
    assert "Use “Retry analysis” after addressing the issue." in content


@pytest.mark.django_db
def test_project_page_detail_view_dedupes_when_active_run_exists(client, monkeypatch):
    user = User.objects.create_user(
        username="page-detail-dedupe-user",
        email="page-detail-dedupe-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        type_ai_guess="product page",
    )

    ProjectPageAnalysisRun.objects.create(
        project=project,
        project_page=page,
        requested_by=user.profile,
        status=ProjectPageAnalysisRun.Status.RUNNING,
    )

    monkeypatch.setattr(
        user.profile.__class__,
        "is_on_pro_plan",
        property(lambda _self: True),
    )

    client.force_login(user)
    response = client.post(
        reverse(
            "project_page_detail",
            kwargs={"project_pk": project.id, "page_pk": page.id},
        ),
        data={"action": "run_seo_analysis"},
        follow=True,
    )

    assert response.status_code == 200
    assert (
        ProjectPageAnalysisRun.objects.filter(
            project_page=page,
            status__in=[
                ProjectPageAnalysisRun.Status.QUEUED,
                ProjectPageAnalysisRun.Status.RUNNING,
            ],
        ).count()
        == 1
    )
    assert "Analysis is already running for this page" in response.content.decode()
