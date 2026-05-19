import pytest
from django.urls import reverse

from core.choices import BlogPostStatus
from core.models import BlogPost


@pytest.mark.django_db
def test_blog_post_view_returns_most_recent_published_post_when_slug_is_duplicated(client):
    slug = "duplicate-slug"

    older_post = BlogPost.objects.create(
        title="Older",
        description="old description",
        slug=slug,
        tags="seo",
        content="old content",
        status=BlogPostStatus.PUBLISHED,
    )
    newer_post = BlogPost.objects.create(
        title="Newer",
        description="new description",
        slug=slug,
        tags="seo",
        content="new content",
        status=BlogPostStatus.PUBLISHED,
    )

    response = client.get(reverse("blog_post", kwargs={"slug": slug}))

    assert response.status_code == 200
    assert response.context["blog_post"].id == newer_post.id
    assert response.context["blog_post"].id != older_post.id
