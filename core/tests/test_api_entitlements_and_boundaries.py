from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core.api.schemas import AddPricingPageIn, FixGeneratedBlogPostIn, GenerateCompetitorVsTitleIn, PostGeneratedBlogPostIn
from core.api.views import (
    add_pricing_page,
    fix_generated_blog_post,
    generate_competitor_vs_title,
    post_generated_blog_post,
)
from core.models import Competitor, GeneratedBlogPost, Project


@pytest.mark.django_db
def test_add_pricing_page_blocks_cross_project_access(monkeypatch):
    owner = User.objects.create_user("owner-pricing", "owner-pricing@example.com", "secret")
    attacker = User.objects.create_user("attacker-pricing", "attacker-pricing@example.com", "secret")

    project = Project.objects.create(profile=owner.profile, name="Owner", url="https://owner.test")

    monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_args, **_kwargs: None)

    request = SimpleNamespace(auth=attacker.profile)
    response = add_pricing_page(
        request,
        AddPricingPageIn(project_id=project.id, url="https://owner.test/pricing"),
    )

    assert response["status"] == "error"
    assert response["code"] == "RESOURCE_NOT_FOUND"
    assert response["message"] == "Project not found"


@pytest.mark.django_db
def test_generate_competitor_vs_title_blocks_cross_project_access(monkeypatch):
    owner = User.objects.create_user("owner-competitor", "owner-competitor@example.com", "secret")
    attacker = User.objects.create_user("attacker-competitor", "attacker-competitor@example.com", "secret")

    project = Project.objects.create(profile=owner.profile, name="Owner", url="https://owner.test")
    competitor = Competitor.objects.create(
        project=project,
        name="Rival",
        url="https://rival.test",
        description="Rival desc",
    )

    monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_args, **_kwargs: None)

    request = SimpleNamespace(auth=attacker.profile)
    response = generate_competitor_vs_title(
        request,
        GenerateCompetitorVsTitleIn(competitor_id=competitor.id),
    )

    assert response["status"] == "error"
    assert response["code"] == "RESOURCE_NOT_FOUND"
    assert response["message"] == "Competitor not found"


@pytest.mark.django_db
def test_post_generated_blog_post_blocks_cross_project_access_with_deterministic_error():
    owner = User.objects.create_user("owner-post", "owner-post@example.com", "secret")
    attacker = User.objects.create_user("attacker-post", "attacker-post@example.com", "secret")

    project = Project.objects.create(profile=owner.profile, name="Owner", url="https://owner.test")
    generated_post = GeneratedBlogPost.objects.create(
        project=project,
        title="Title",
        slug="title",
        tags="tag",
        content="# Post",
    )

    request = SimpleNamespace(auth=attacker.profile)
    response = post_generated_blog_post(request, PostGeneratedBlogPostIn(id=generated_post.id))

    assert response["status"] == "error"
    assert response["code"] == "RESOURCE_NOT_FOUND"
    assert response["message"] == "Generated blog post not found"


@pytest.mark.django_db
def test_fix_generated_blog_post_blocks_cross_project_access_with_deterministic_error():
    owner = User.objects.create_user("owner-fix", "owner-fix@example.com", "secret")
    attacker = User.objects.create_user("attacker-fix", "attacker-fix@example.com", "secret")

    project = Project.objects.create(profile=owner.profile, name="Owner", url="https://owner.test")
    generated_post = GeneratedBlogPost.objects.create(
        project=project,
        title="Title",
        slug="title",
        tags="tag",
        content="# Post",
    )

    request = SimpleNamespace(auth=attacker.profile)
    response = fix_generated_blog_post(request, FixGeneratedBlogPostIn(id=generated_post.id))

    assert response["status"] == "error"
    assert response["code"] == "RESOURCE_NOT_FOUND"
    assert response["message"] == "Generated blog post not found"
