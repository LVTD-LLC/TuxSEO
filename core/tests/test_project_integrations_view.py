import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Project


def create_user_with_project(username: str) -> tuple[User, Project]:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url=f"https://{username}.example.com",
        name=f"{username} project",
    )
    return user, project


@pytest.mark.django_db
def test_project_integrations_view_renders_expected_cards(client):
    user, project = create_user_with_project("project-integrations-owner")
    client.force_login(user)

    response = client.get(reverse("project_integrations", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Integrations" in content
    assert "Google Analytics (GA4)" in content
    assert "Google Search Console (GSC)" in content
    assert "Plausible" in content
    assert reverse("project_integrations", kwargs={"pk": project.id}) in content


@pytest.mark.django_db
def test_project_integrations_view_blocks_access_to_other_users_project(client):
    _, project = create_user_with_project("project-integrations-owner-2")
    other_user = User.objects.create_user(
        username="project-integrations-other-user",
        email="project-integrations-other-user@example.com",
        password="secret",
    )
    client.force_login(other_user)

    response = client.get(reverse("project_integrations", kwargs={"pk": project.id}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_project_integrations_view_requires_login(client):
    _, project = create_user_with_project("project-integrations-owner-3")

    response = client.get(reverse("project_integrations", kwargs={"pk": project.id}))

    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))
