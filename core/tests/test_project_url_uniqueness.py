from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import RequestFactory, override_settings

from core.api.schemas import ProjectScanIn
from core.api.views import create_project
from core.models import Project


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=False)
def test_create_project_allows_same_url_for_different_profiles(monkeypatch):
    monkeypatch.setattr("core.models.Project.get_page_content", lambda self: True)
    monkeypatch.setattr("core.models.Project.analyze_content", lambda self: True)

    user_a = User.objects.create_user(
        username="agency-a",
        email="agency-a@example.com",
        password="secret",
    )
    user_b = User.objects.create_user(
        username="agency-b",
        email="agency-b@example.com",
        password="secret",
    )

    request_a = RequestFactory().post("/api/projects/")
    request_a.auth = user_a.profile

    request_b = RequestFactory().post("/api/projects/")
    request_b.auth = user_b.profile

    project_url = "https://client-domain.com"

    with patch("core.api.views.async_task") as mock_async_task:
        response_a = create_project(
            request_a,
            ProjectScanIn(url=project_url, source="onboarding_modal"),
        )
        response_b = create_project(
            request_b,
            ProjectScanIn(url=project_url, source="onboarding_modal"),
        )

    sitemap_discovery_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args
        and call.args[0] == "core.tasks.auto_discover_and_ingest_sitemap"
    ]

    assert response_a["status"] == "success"
    assert response_b["status"] == "success"
    assert "description" in response_a
    assert "description" in response_b

    assert Project.objects.filter(url=project_url).count() == 2
    assert Project.objects.filter(profile=user_a.profile, url=project_url).count() == 1
    assert Project.objects.filter(profile=user_b.profile, url=project_url).count() == 1
    assert len(sitemap_discovery_calls) == 2


@pytest.mark.django_db
def test_project_url_is_unique_per_profile_at_database_level():
    user = User.objects.create_user(
        username="one-profile",
        email="one-profile@example.com",
        password="secret",
    )

    Project.objects.create(profile=user.profile, url="https://duplicate.com", name="First")

    with pytest.raises(IntegrityError):
        Project.objects.create(profile=user.profile, url="https://duplicate.com", name="Second")


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=False)
def test_create_project_blocks_duplicate_url_for_same_profile():
    user = User.objects.create_user(
        username="same-user",
        email="same-user@example.com",
        password="secret",
    )
    Project.objects.create(profile=user.profile, url="https://same-client.com", name="Existing")

    request = RequestFactory().post("/api/projects/")
    request.auth = user.profile

    response = create_project(
        request,
        ProjectScanIn(url="https://same-client.com", source="onboarding_modal"),
    )

    assert response["status"] == "error"
    assert response["message"] == "You already added this project URL"
