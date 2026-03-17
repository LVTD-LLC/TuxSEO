from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core.choices import ProfileStates, ProjectPageSource
from core.models import Profile, Project, ProjectPage
from core.tasks import parse_sitemap_and_save_urls, sync_all_projects_with_sitemaps


class DummyResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.ok = status_code == 200


@pytest.mark.django_db
@patch("core.tasks.async_task")
def test_parse_sitemap_sync_upserts_and_marks_stale(mock_async_task, monkeypatch):
    user = User.objects.create_user("sitemap-sync-user", "sync@example.com", "pass")
    profile = Profile.objects.get(user=user)

    project = Project.objects.create(
        profile=profile,
        url="https://example.com",
        name="Example",
        sitemap_url="https://example.com/sitemap.xml",
    )

    stale_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/old-page",
        source=ProjectPageSource.SITEMAP,
    )
    active_page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/kept-page",
        source=ProjectPageSource.SITEMAP,
        sitemap_is_stale=True,
    )

    sitemap_index_xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <sitemap><loc>https://example.com/child.xml</loc></sitemap>
    </sitemapindex>
    """
    child_xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://example.com/kept-page</loc></url>
      <url><loc>https://example.com/new-page</loc></url>
    </urlset>
    """

    def fake_get(url, timeout):
        if url == "https://example.com/sitemap.xml":
            return DummyResponse(sitemap_index_xml)
        if url == "https://example.com/child.xml":
            return DummyResponse(child_xml)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr("core.tasks.requests.get", fake_get)

    result = parse_sitemap_and_save_urls(project.id)

    assert "Sitemap sync completed" in result

    stale_page.refresh_from_db()
    active_page.refresh_from_db()

    new_page = ProjectPage.objects.get(project=project, url="https://example.com/new-page")
    assert new_page.source == ProjectPageSource.SITEMAP
    assert new_page.sitemap_is_stale is False
    assert new_page.sitemap_last_seen_at is not None

    assert stale_page.sitemap_is_stale is True
    assert active_page.sitemap_is_stale is False
    assert active_page.sitemap_last_seen_at is not None

    mock_async_task.assert_called_once_with(
        "core.tasks.analyze_sitemap_pages",
        project.id,
        group="Analyze Sitemap Pages",
    )


@pytest.mark.django_db
@patch("core.tasks.cache.add", return_value=False)
def test_parse_sitemap_sync_skips_when_lock_is_held(_mock_cache_add):
    user = User.objects.create_user("sitemap-lock-user", "lock@example.com", "pass")
    profile = Profile.objects.get(user=user)
    project = Project.objects.create(
        profile=profile,
        url="https://example.org",
        name="Example Org",
        sitemap_url="https://example.org/sitemap.xml",
    )

    result = parse_sitemap_and_save_urls(project.id)

    assert "already running" in result


@pytest.mark.django_db
def test_sync_all_projects_with_sitemaps_skips_disabled_profile_and_invalid_urls(monkeypatch):
    enabled_user = User.objects.create_user("enabled", "enabled@example.com", "pass")
    enabled_profile = Profile.objects.get(user=enabled_user)

    disabled_user = User.objects.create_user("disabled", "disabled@example.com", "pass")
    disabled_profile = Profile.objects.get(user=disabled_user)
    disabled_profile.state = ProfileStates.ACCOUNT_DELETED
    disabled_profile.save(update_fields=["state"])

    valid_project = Project.objects.create(
        profile=enabled_profile,
        url="https://valid.example",
        name="Valid",
        sitemap_url="https://valid.example/sitemap.xml",
    )
    Project.objects.create(
        profile=enabled_profile,
        url="https://invalid.example",
        name="Invalid",
        sitemap_url="not-a-url",
    )
    Project.objects.create(
        profile=disabled_profile,
        url="https://disabled.example",
        name="Disabled",
        sitemap_url="https://disabled.example/sitemap.xml",
    )

    def fake_parse(project_id, return_summary=False):
        if project_id == valid_project.id:
            return {
                "status": "success",
                "message": "Sitemap sync completed for Valid",
            }
        return {
            "status": "skipped",
            "message": "No valid sitemap URL found for project Invalid.",
        }

    monkeypatch.setattr("core.tasks.parse_sitemap_and_save_urls", fake_parse)

    result = sync_all_projects_with_sitemaps()

    assert "processed=2" in result
    assert "succeeded=1" in result
    assert "skipped=2" in result
