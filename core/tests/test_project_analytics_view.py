from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import AnalyticsFactDaily, Project, ProjectIntegration


def create_user_with_project(username: str, project_url: str) -> tuple[User, Project]:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url=project_url,
        name="Analytics Project",
    )
    return user, project


@pytest.mark.django_db
def test_project_analytics_view_requires_login(client):
    _, project = create_user_with_project(
        username="project-analytics-login-user",
        project_url="https://analytics-login.example.com",
    )

    response = client.get(reverse("project_analytics", kwargs={"pk": project.id}))

    assert response.status_code == 302
    assert "/accounts/login" in response.url


@pytest.mark.django_db
def test_project_analytics_view_blocks_other_project_access(client):
    _, project = create_user_with_project(
        username="project-analytics-owner-user",
        project_url="https://analytics-owner.example.com",
    )
    other_user = User.objects.create_user(
        username="project-analytics-other-user",
        email="project-analytics-other-user@example.com",
        password="secret",
    )

    client.force_login(other_user)
    response = client.get(reverse("project_analytics", kwargs={"pk": project.id}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_project_analytics_view_renders_shell_and_empty_state(client):
    user, project = create_user_with_project(
        username="project-analytics-empty-user",
        project_url="https://analytics-empty.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_analytics", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Analytics" in content
    assert "Overview KPIs" in content
    assert "Traffic / Engagement" in content
    assert "Conversions / Revenue" in content
    assert "Data source health / status" in content
    assert "No analytics integrations are connected yet." in content
    assert reverse("project_integrations", kwargs={"pk": project.id}) in content


@pytest.mark.django_db
def test_project_analytics_view_handles_partial_integrations_without_crashing(client):
    user, project = create_user_with_project(
        username="project-analytics-partial-user",
        project_url="https://analytics-partial.example.com",
    )

    ProjectIntegration.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE,
        status=ProjectIntegration.Status.CONNECTED,
    )

    today = timezone.now().date()
    AnalyticsFactDaily.objects.create(
        project=project,
        provider=AnalyticsFactDaily.Provider.GSC,
        metric_date=today - timedelta(days=1),
        dimension_scope=AnalyticsFactDaily.DimensionScope.PAGE_QUERY,
        page_url="https://analytics-partial.example.com/pricing",
        search_query="tux seo pricing",
        dimension_fingerprint="partial-gsc-row",
        clicks=3,
        impressions=160,
        ctr=0.018,
        avg_position=12.3,
    )

    client.force_login(user)
    response = client.get(reverse("project_analytics", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "GSC Connected" in content
    assert "GA4 Not connected" in content
    assert "Plausible Not connected" in content
    assert "tux seo pricing" in content


@pytest.mark.django_db
def test_project_home_links_to_project_analytics_page(client):
    user, project = create_user_with_project(
        username="project-home-analytics-link-user",
        project_url="https://analytics-link.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert reverse("project_analytics", kwargs={"pk": project.id}) in content
    assert "Open Analytics page" in content
