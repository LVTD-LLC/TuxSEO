from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils import timezone

from core.models import AnalyticsFactDaily, AnalyticsSyncCursor, Project, ProjectIntegration


def _create_user_with_project(username: str) -> tuple[User, Project]:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url=f"https://{username}.example.com",
        name="Analytics API Project",
    )
    return user, project


@pytest.mark.django_db
def test_project_analytics_aggregation_requires_login(client):
    _, project = _create_user_with_project("analytics-api-auth")

    response = client.get(f"/api/projects/{project.id}/analytics/aggregation")

    assert response.status_code in {401, 302}


@pytest.mark.django_db
def test_project_analytics_aggregation_blocks_cross_project_access(client):
    owner, project = _create_user_with_project("analytics-api-owner")
    attacker = User.objects.create_user(
        username="analytics-api-attacker",
        email="analytics-api-attacker@example.com",
        password="secret",
    )
    assert owner.id != attacker.id

    client.force_login(attacker)
    response = client.get(f"/api/projects/{project.id}/analytics/aggregation")

    assert response.status_code == 404


@pytest.mark.django_db
def test_project_analytics_aggregation_validates_date_range(client):
    user, project = _create_user_with_project("analytics-api-dates")
    client.force_login(user)

    response = client.get(
        f"/api/projects/{project.id}/analytics/aggregation",
        {"start_date": "2026-03-10", "end_date": "2026-03-01"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert "start_date" in payload["message"]


@pytest.mark.django_db
def test_project_analytics_aggregation_includes_partial_source_health(client):
    user, project = _create_user_with_project("analytics-api-partial")

    ProjectIntegration.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.GOOGLE_ANALYTICS,
        status=ProjectIntegration.Status.CONNECTED,
    )
    ProjectIntegration.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE,
        status=ProjectIntegration.Status.CONNECTED,
    )

    now = timezone.now()
    AnalyticsSyncCursor.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.GOOGLE_ANALYTICS,
        source_account_ref="ga4:property:1",
        last_status=AnalyticsSyncCursor.SyncStatus.SUCCESS,
        last_run_finished_at=now,
    )
    AnalyticsSyncCursor.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE,
        source_account_ref="gsc:property:1",
        last_status=AnalyticsSyncCursor.SyncStatus.PARTIAL,
        last_run_finished_at=now,
        last_error="rate limited",
    )

    AnalyticsFactDaily.objects.create(
        project=project,
        provider=AnalyticsFactDaily.Provider.GA4,
        metric_date=now.date() - timedelta(days=1),
        dimension_scope=AnalyticsFactDaily.DimensionScope.SITE,
        dimension_fingerprint="ga4-site-row",
        sessions=120,
        users=90,
        conversions=12,
    )

    client.force_login(user)
    response = client.get(f"/api/projects/{project.id}/analytics/aggregation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["message"].startswith("Partial source health issues detected")

    health_by_source = {row["source"]: row for row in payload["source_health"]}
    assert health_by_source["ga4"]["status"] == "healthy"
    assert health_by_source["gsc"]["status"] == "degraded"
    assert health_by_source["gsc"]["last_error"] == "rate limited"


@pytest.mark.django_db
def test_project_analytics_aggregation_uses_cache(client):
    cache.clear()
    user, project = _create_user_with_project("analytics-api-cache")
    client.force_login(user)

    first = client.get(f"/api/projects/{project.id}/analytics/aggregation")
    second = client.get(f"/api/projects/{project.id}/analytics/aggregation")

    assert first.status_code == 200
    assert second.status_code == 200

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["cached"] is False
    assert second_payload["cached"] is True
    assert first_payload["cache_key"] == second_payload["cache_key"]
