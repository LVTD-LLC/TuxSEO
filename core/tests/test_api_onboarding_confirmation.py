import pytest
from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory

from core.api.schemas import ConfirmProjectOnboardingIn
from core.api.views import confirm_project_onboarding
from core.models import Project


@pytest.mark.django_db
def test_confirm_project_onboarding_updates_name_summary_and_description():
    user = User.objects.create_user(
        username="onboarding-confirm-user",
        email="onboarding-confirm@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Initial Name",
        summary="Old summary",
        description="Old description",
    )

    request = RequestFactory().post(f"/api/projects/{project.id}/confirm-onboarding")
    request.auth = user.profile

    response = confirm_project_onboarding(
        request,
        project.id,
        ConfirmProjectOnboardingIn(
            name="  Better Name  ",
            summary="  Better summary  ",
            description="  Better description  ",
        ),
    )

    project.refresh_from_db()

    assert response["status"] == "success"
    assert project.name == "Better Name"
    assert project.summary == "Better summary"
    assert project.description == "Better description"


@pytest.mark.django_db
def test_confirm_project_onboarding_disallows_access_to_other_profiles_project():
    owner = User.objects.create_user(
        username="project-owner",
        email="project-owner@example.com",
        password="secret",
    )
    other_user = User.objects.create_user(
        username="other-user",
        email="other-user@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=owner.profile,
        url="https://owner-site.com",
        name="Owner Project",
    )

    request = RequestFactory().post(f"/api/projects/{project.id}/confirm-onboarding")
    request.auth = other_user.profile

    with pytest.raises(Http404):
        confirm_project_onboarding(
            request,
            project.id,
            ConfirmProjectOnboardingIn(name="Should Not Work"),
        )
