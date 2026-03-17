from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import GeneratedBlogPost, Project, ProjectEarnedLink, ProjectPage


PROJECT_NAVIGATION_TEMPLATE_PATH = Path("frontend/templates/components/project_navigation.html")


def create_user_with_project(username: str, project_url: str) -> tuple[User, Project]:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url=project_url,
        name=f"{username} project",
    )
    return user, project


def test_project_navigation_includes_earned_links_route():
    content = PROJECT_NAVIGATION_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "project_earned_links" in content
    assert "Earned Links" in content


@pytest.mark.django_db
def test_project_earned_links_view_lists_inbound_entries(client):
    owner, target_project = create_user_with_project(
        "earned-links-target-owner", "https://target.example.com"
    )
    _, source_project = create_user_with_project("earned-links-source-owner", "https://source.example.com")

    source_post = GeneratedBlogPost.objects.create(
        project=source_project,
        title="Source post",
        slug="source-post",
        tags="seo",
        content="Some content",
    )
    target_page = ProjectPage.objects.create(
        project=target_project,
        url="https://target.example.com/docs",
        type_ai_guess="docs",
    )

    ProjectEarnedLink.objects.create(
        source_project=source_project,
        target_project=target_project,
        source_generated_blog_post=source_post,
        source_page_title=source_post.title,
        target_page=target_page,
        target_page_url=target_page.url,
    )

    client.force_login(owner)
    response = client.get(reverse("project_earned_links", kwargs={"pk": target_project.id}))

    assert response.status_code == 200
    assert response.context["total_earned_links_count"] == 1
    assert response.context["unique_source_projects_count"] == 1

    content = response.content.decode()
    assert "Earned Links" in content
    assert "Source post" in content
    assert "https://target.example.com/docs" in content


@pytest.mark.django_db
def test_project_earned_links_view_blocks_access_to_other_users_project(client):
    _, target_project = create_user_with_project(
        "earned-links-target-owner-2", "https://target2.example.com"
    )
    other_user = User.objects.create_user(
        username="earned-links-other-user",
        email="earned-links-other-user@example.com",
        password="secret",
    )

    client.force_login(other_user)
    response = client.get(reverse("project_earned_links", kwargs={"pk": target_project.id}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_record_link_placement_creates_deduped_earned_link_entry():
    owner, source_project = create_user_with_project("earned-links-source", "https://source3.example.com")
    target_project = Project.objects.create(
        profile=owner.profile,
        url="https://target3.example.com",
        name="Target project",
    )

    target_page = ProjectPage.objects.create(
        project=target_project,
        url="https://target3.example.com/features",
        type_ai_guess="feature",
    )

    source_post = GeneratedBlogPost.objects.create(
        project=source_project,
        title="How to do SEO",
        slug="how-to-do-seo",
        tags="seo",
        content="Body",
    )

    source_post._record_link_placement_audit_logs(
        candidate_pages=[target_page],
        content_with_links="Use [feature docs](https://target3.example.com/features)",
    )
    source_post._record_link_placement_audit_logs(
        candidate_pages=[target_page],
        content_with_links="Use [feature docs](https://target3.example.com/features)",
    )

    links = ProjectEarnedLink.objects.filter(
        source_project=source_project,
        target_project=target_project,
        source_page_url="https://source3.example.com/how-to-do-seo",
        target_page_url=target_page.url,
    )

    assert links.count() == 1
    assert links.first().last_anchor == "feature docs"
