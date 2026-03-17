from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core import integration_analytics as analytics
from core.models import (
    AnalyticsFactDaily,
    AnalyticsSourceSnapshot,
    AnalyticsSyncCursor,
    Project,
    ProjectIntegration,
)


def _create_project() -> Project:
    user = User.objects.create_user(
        username=f"analytics-{timezone.now().timestamp()}",
        email="analytics@example.com",
        password="secret",
    )
    return Project.objects.create(profile=user.profile, url="https://example.com", name="Example")


@pytest.mark.django_db
def test_sync_skips_when_integration_missing():
    project = _create_project()

    result = analytics.sync_project_provider_analytics(
        project_id=project.id,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
    )

    assert result["status"] == "skipped"
    assert AnalyticsSourceSnapshot.objects.count() == 0
    assert AnalyticsFactDaily.objects.count() == 0


@pytest.mark.django_db
def test_incremental_cursor_reduces_window_on_subsequent_runs(monkeypatch):
    project = _create_project()
    ProjectIntegration.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
        status=ProjectIntegration.Status.CONNECTED,
        plausible_api_key="plausible-key",
        plausible_site_id="example.com",
        plausible_base_url="https://plausible.io",
    )

    captured_windows = []

    def fake_fetch(*, integration, start_date, end_date):
        captured_windows.append((start_date, end_date))
        rows = [
            analytics.CanonicalFactRow(
                metric_date=end_date,
                dimension_scope=AnalyticsFactDaily.DimensionScope.PAGE,
                page_url="https://example.com/",
                sessions=10,
                users=9,
                bounce_rate=Decimal("0.25"),
            )
        ]
        return rows, {"ok": True}, "example.com"

    monkeypatch.setattr(analytics, "_fetch_plausible_rows", fake_fetch)

    first = analytics.sync_project_provider_analytics(
        project_id=project.id,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
    )
    second = analytics.sync_project_provider_analytics(
        project_id=project.id,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
    )

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert len(captured_windows) == 2

    first_window_days = (captured_windows[0][1] - captured_windows[0][0]).days
    second_window_days = (captured_windows[1][1] - captured_windows[1][0]).days
    assert first_window_days >= 80
    assert second_window_days <= 3

    assert AnalyticsSourceSnapshot.objects.count() == 2
    assert AnalyticsFactDaily.objects.count() == 1  # idempotent upsert
    cursor = AnalyticsSyncCursor.objects.get(project=project, provider=ProjectIntegration.Provider.PLAUSIBLE)
    assert cursor.last_status == AnalyticsSyncCursor.SyncStatus.SUCCESS


@pytest.mark.django_db
def test_sync_records_failure_and_cursor_status(monkeypatch):
    project = _create_project()
    ProjectIntegration.objects.create(
        project=project,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
        status=ProjectIntegration.Status.CONNECTED,
        plausible_api_key="plausible-key",
        plausible_site_id="example.com",
    )

    def rate_limited(*args, **kwargs):
        raise analytics.ProviderRateLimitError("limited", retry_after_seconds=2)

    monkeypatch.setattr(analytics, "_fetch_plausible_rows", rate_limited)

    result = analytics.sync_project_provider_analytics(
        project_id=project.id,
        provider=ProjectIntegration.Provider.PLAUSIBLE,
    )

    assert result["status"] == "failed"
    cursor = AnalyticsSyncCursor.objects.get(project=project, provider=ProjectIntegration.Provider.PLAUSIBLE)
    assert cursor.last_status == AnalyticsSyncCursor.SyncStatus.PARTIAL
    assert "limited" in cursor.last_error

    snapshot = AnalyticsSourceSnapshot.objects.get(project=project)
    assert snapshot.status == AnalyticsSourceSnapshot.FetchStatus.FAILED
    assert snapshot.error_code == "rate_limited"


def test_fetch_plausible_rows_uses_provider_dates(monkeypatch):
    class DummyResponse:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {
                        "dimensions": ["2026-03-01", "/blog"],
                        "metrics": [5, 7, 54.3],
                    }
                ]
            }

    monkeypatch.setattr(analytics, "_request_with_backoff", lambda **kwargs: DummyResponse())

    class Integration:
        plausible_api_key = "k"
        plausible_site_id = "example.com"
        plausible_base_url = "https://plausible.io"

    rows, _, _ = analytics._fetch_plausible_rows(
        integration=Integration(),
        start_date=timezone.datetime(2026, 3, 1).date(),
        end_date=timezone.datetime(2026, 3, 1).date(),
    )

    assert len(rows) == 1
    assert rows[0].metric_date.isoformat() == "2026-03-01"
    assert rows[0].bounce_rate == Decimal("0.543")


def test_request_with_backoff_retries_429_then_succeeds(monkeypatch):
    class DummyResponse:
        def __init__(self, status_code, headers=None, text="", payload=None):
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text
            self._payload = payload or {}

        def json(self):
            return self._payload

    calls = {"count": 0}

    def fake_request(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return DummyResponse(429, headers={"Retry-After": "0"})
        return DummyResponse(200, payload={"ok": True})

    monkeypatch.setattr(analytics.requests, "request", fake_request)
    monkeypatch.setattr(analytics.time, "sleep", lambda *_: None)

    response = analytics._request_with_backoff(method="GET", url="https://example.com")

    assert response.status_code == 200
    assert calls["count"] == 2
