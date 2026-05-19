import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory, override_settings

from core.api.schemas import ProjectScanIn
from core.api.views import create_project, generate_blog_content
from core.models import Project


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
def test_create_project_blocks_unverified_user_before_project_creation():
    user = User.objects.create_user(
        username="api-unverified-user",
        email="api-unverified@example.com",
        password="secret",
    )

    request = RequestFactory().post("/api/projects/")
    request.auth = user.profile

    response = create_project(request, ProjectScanIn(url="https://example.com", source="default"))

    assert response["status"] == "error"
    assert "verify your email" in response["message"]


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
def test_generate_blog_content_blocks_unverified_user_before_lookup():
    user = User.objects.create_user(
        username="api-unverified-blog-user",
        email="api-unverified-blog@example.com",
        password="secret",
    )

    request = RequestFactory().post("/api/generate-blog-content/999/")
    request.auth = user.profile

    response = generate_blog_content(request, suggestion_id=999)

    assert response["status"] == "error"
    assert response["task_id"] is None
    assert "verify your email" in response["message"]


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
def test_create_project_allows_unverified_user_for_first_onboarding_project(monkeypatch):
    monkeypatch.setattr("core.models.Project.get_page_content", lambda self: True)
    monkeypatch.setattr("core.models.Project.analyze_content", lambda self: True)

    user = User.objects.create_user(
        username="api-onboarding-user",
        email="api-onboarding@example.com",
        password="secret",
    )

    request = RequestFactory().post("/api/projects/")
    request.auth = user.profile

    response = create_project(
        request,
        ProjectScanIn(url="https://first-project.example.com", source="onboarding_modal"),
    )

    assert response["status"] == "success"
    assert Project.objects.filter(profile=user.profile).count() == 1


@pytest.mark.django_db
@override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
def test_create_project_blocks_unverified_user_on_second_onboarding_project():
    user = User.objects.create_user(
        username="api-onboarding-second-user",
        email="api-onboarding-second@example.com",
        password="secret",
    )
    Project.objects.create(
        profile=user.profile,
        url="https://existing-project.example.com",
        name="Existing",
    )

    request = RequestFactory().post("/api/projects/")
    request.auth = user.profile

    response = create_project(
        request,
        ProjectScanIn(url="https://second-project.example.com", source="onboarding_modal"),
    )

    assert response["status"] == "error"
    assert "verify your email" in response["message"]
