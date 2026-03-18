import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Project, ProjectPage


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
def test_project_page_detail_view_allows_pro_users(client):
    user = User.objects.create_superuser(
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
    assert "SEO analysis failed to load. Please retry." in content
    assert "Could not load backlink opportunities. Please retry." in content
