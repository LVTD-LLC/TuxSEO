import pytest
from django.contrib.auth.models import User

from core.models import Project, ProjectPage
from core.tasks import refresh_backlink_prospects_cache


@pytest.mark.django_db
def test_refresh_backlink_prospects_cache_task_smoke_success(monkeypatch):
    user = User.objects.create_user(
        username="backlink-refresh-task-user",
        email="backlink-refresh-task-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/features",
        type_ai_guess="product page",
    )

    monkeypatch.setattr(
        "core.backlink_prospects.discover_backlink_prospects",
        lambda _page: [{"url": "https://example.org/guide", "source": "exa"}],
    )

    result = refresh_backlink_prospects_cache(page.id)

    assert result == f"Cached 1 backlink prospects for page {page.id}"


@pytest.mark.django_db
def test_refresh_backlink_prospects_cache_task_handles_missing_page():
    result = refresh_backlink_prospects_cache(project_page_id=999999)

    assert result == "ProjectPage 999999 not found"
