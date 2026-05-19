import json

import pytest
from django.contrib.auth.models import User


@pytest.mark.django_db
class TestInternalBlogPostApi:
    def test_internal_blog_post_endpoints_require_superuser_api_key(self, client):
        user = User.objects.create_user(
            username="regular-api-user",
            email="regular-api-user@example.com",
            password="secret",
        )

        response_without_key = client.get("/api/internal/blog-posts")
        assert response_without_key.status_code == 401

        response_with_non_superuser_key = client.get(
            f"/api/internal/blog-posts?api_key={user.profile.key}"
        )
        assert response_with_non_superuser_key.status_code == 401

    def test_superuser_can_create_and_manage_internal_blog_posts(self, client):
        superuser = User.objects.create_superuser(
            username="superuser-api-user",
            email="superuser-api-user@example.com",
            password="secret",
        )
        api_key = superuser.profile.key

        create_payload = {
            "title": "Initial Internal Post",
            "description": "Initial description",
            "slug": "initial-internal-post",
            "tags": "internal,api",
            "content": "Initial content",
            "status": "DRAFT",
        }

        create_response = client.post(
            f"/api/blog-posts/submit?api_key={api_key}",
            data=json.dumps(create_payload),
            content_type="application/json",
        )
        assert create_response.status_code == 200
        assert create_response.json()["status"] == "success"

        list_response = client.get(f"/api/internal/blog-posts?api_key={api_key}")
        assert list_response.status_code == 200
        list_data = list_response.json()
        assert list_data["status"] == "success"
        assert len(list_data["blog_posts"]) == 1

        blog_post_id = list_data["blog_posts"][0]["id"]

        retrieve_response = client.get(f"/api/internal/blog-posts/{blog_post_id}?api_key={api_key}")
        assert retrieve_response.status_code == 200
        assert retrieve_response.json()["blog_post"]["slug"] == "initial-internal-post"

        patch_response = client.patch(
            f"/api/internal/blog-posts/{blog_post_id}?api_key={api_key}",
            data=json.dumps({"title": "Patched Internal Post"}),
            content_type="application/json",
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["blog_post"]["title"] == "Patched Internal Post"

        put_payload = {
            "title": "Fully Updated Internal Post",
            "description": "Updated description",
            "slug": "fully-updated-internal-post",
            "tags": "internal,api,updated",
            "content": "Updated content",
            "status": "PUBLISHED",
        }
        put_response = client.put(
            f"/api/internal/blog-posts/{blog_post_id}?api_key={api_key}",
            data=json.dumps(put_payload),
            content_type="application/json",
        )
        assert put_response.status_code == 200
        put_data = put_response.json()
        assert put_data["blog_post"]["title"] == "Fully Updated Internal Post"
        assert put_data["blog_post"]["status"] == "PUBLISHED"

        delete_response = client.delete(f"/api/internal/blog-posts/{blog_post_id}?api_key={api_key}")
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "success"

        missing_response = client.get(f"/api/internal/blog-posts/{blog_post_id}?api_key={api_key}")
        assert missing_response.status_code == 404
