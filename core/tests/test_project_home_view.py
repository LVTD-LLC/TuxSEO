from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BlogPostTitleSuggestion,
    Competitor,
    GeneratedBlogPost,
    Keyword,
    Project,
    ProjectKeyword,
    ProjectPage,
)


def create_user_with_project(
    username: str, project_url: str, project_name: str = "Project"
) -> tuple:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url=project_url,
        name=project_name,
    )
    return user, project


@pytest.mark.django_db
def test_project_redirect_points_to_project_home(client):
    user, project = create_user_with_project(
        username="project-home-redirect-user",
        project_url="https://redirect-project.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_redirect", kwargs={"pk": project.id}))

    assert response.status_code == 302
    assert response.url == reverse("project_home", kwargs={"pk": project.id})


@pytest.mark.django_db
def test_project_home_view_returns_content_counts(client):
    user, project = create_user_with_project(
        username="project-home-counts-user",
        project_url="https://counts-project.example.com",
        project_name="Counts Project",
    )
    project.summary = "Project summary for home page."
    project.save(update_fields=["summary"])

    title_suggestion = BlogPostTitleSuggestion.objects.create(
        project=project,
        title="A strong SEO title",
        description="Description",
    )
    GeneratedBlogPost.objects.create(
        project=project,
        title_suggestion=title_suggestion,
        title="Generated post",
        slug="generated-post",
        tags="seo,content",
        content="Generated content",
        posted=True,
    )
    keyword = Keyword.objects.create(keyword_text="project home keyword")
    ProjectKeyword.objects.create(project=project, keyword=keyword, use=True)
    ProjectPage.objects.create(
        project=project,
        url="https://counts-project.example.com/features",
        type_ai_guess="product page",
    )
    Competitor.objects.create(
        project=project,
        name="Competitor One",
        url="https://competitor.example.com",
        description="Competitor description",
    )

    client.force_login(user)
    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    assert response.context["title_ideas_state"]["count"] == 1
    assert response.context["keywords_state"]["count"] == 1
    assert response.context["pages_state"]["count"] == 1
    assert response.context["competitors_state"]["count"] == 1
    assert response.context["generated_posts_count"] == 1
    assert response.context["posted_posts_count"] == 1
    assert response.context["has_loading_generation_state"] is False
    assert response.context["has_empty_generation_state"] is False
    assert "Project Home" in response.content.decode()
    assert "Project summary for home page." in response.content.decode()


@pytest.mark.django_db
def test_project_home_view_uses_loading_state_for_recent_project(client):
    user, project = create_user_with_project(
        username="project-home-loading-user",
        project_url="https://loading-project.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    assert response.context["title_ideas_state"]["is_loading_state"] is True
    assert response.context["keywords_state"]["is_loading_state"] is True
    assert response.context["pages_state"]["is_loading_state"] is True
    assert response.context["competitors_state"]["is_loading_state"] is True
    assert response.context["is_summary_loading_state"] is True
    assert response.context["has_loading_generation_state"] is True


@pytest.mark.django_db
def test_project_home_view_uses_empty_state_for_older_project(client):
    user, project = create_user_with_project(
        username="project-home-empty-user",
        project_url="https://empty-project.example.com",
    )
    old_created_date = timezone.now() - timedelta(days=2)
    Project.objects.filter(id=project.id).update(created_at=old_created_date)
    project.refresh_from_db()

    client.force_login(user)
    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    assert response.context["title_ideas_state"]["is_empty_state"] is True
    assert response.context["keywords_state"]["is_empty_state"] is True
    assert response.context["pages_state"]["is_empty_state"] is True
    assert response.context["competitors_state"]["is_empty_state"] is True
    assert response.context["is_summary_empty_state"] is True
    assert response.context["has_empty_generation_state"] is True


@pytest.mark.django_db
def test_project_home_view_blocks_access_to_other_users_project(client):
    owner_user, project = create_user_with_project(
        username="project-home-owner-user",
        project_url="https://owner-project.example.com",
    )
    other_user = User.objects.create_user(
        username="project-home-other-user",
        email="project-home-other-user@example.com",
        password="secret",
    )

    client.force_login(other_user)
    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert owner_user != other_user
    assert response.status_code == 404


@pytest.mark.django_db
def test_project_home_view_renders_how_to_use_block_with_expected_links(client):
    user, project = create_user_with_project(
        username="project-home-how-to-user",
        project_url="https://how-to-project.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "How to use TuxSEO" in content
    assert reverse("project_seo_posts", kwargs={"pk": project.id}) in content
    assert reverse("project_keywords", kwargs={"pk": project.id}) in content
    assert reverse("settings") in content
    assert "/public-api/docs" in content


@pytest.mark.django_db
def test_project_home_view_renders_copyable_api_and_prompt_snippets(client):
    user, project = create_user_with_project(
        username="project-home-copy-snippets-user",
        project_url="https://copy-snippets-project.example.com",
    )
    client.force_login(user)

    response = client.get(reverse("project_home", kwargs={"pk": project.id}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "curl -X GET" in content
    assert f"X-API-Key: {user.profile.key}" in content
    assert "http://testserver/public-api/account" in content
    assert "Security note: treat this key like a password." in content
    assert "You are helping me operate TuxSEO for my project." in content
    assert content.count('data-controller="copy"') >= 2
    assert content.count("click->copy#copy") >= 2
