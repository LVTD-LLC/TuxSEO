import json

import pytest
from django.contrib.auth.models import User

from core.choices import CompetitorPostGenerationStatus
from core.models import Competitor, Project


@pytest.mark.django_db
class TestCompetitorPostGenerationApi:
    def test_generate_endpoint_queues_async_job_and_persists_processing_state(self, client, monkeypatch):
        user = User.objects.create_user(
            username="competitor-user",
            email="competitor-user@example.com",
            password="secret",
        )
        project = Project.objects.create(
            profile=user.profile,
            name="Acme",
            url="https://acme.test",
        )
        competitor = Competitor.objects.create(
            project=project,
            name="Rival",
            url="https://rival.test",
            description="Competing product",
        )

        queued = {}

        def fake_async_task(task_name, competitor_id, **kwargs):
            queued["task_name"] = task_name
            queued["competitor_id"] = competitor_id
            queued["group"] = kwargs.get("group")
            return "task-123"

        monkeypatch.setattr("core.api.views.async_task", fake_async_task)
        monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_args, **_kwargs: None)

        client.force_login(user)
        response = client.post(
            "/api/generate-competitor-vs-title",
            data=json.dumps({"competitor_id": competitor.id}),
            content_type="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "processing"
        assert payload["competitor_id"] == competitor.id

        competitor.refresh_from_db()
        assert competitor.blog_post_generation_status == CompetitorPostGenerationStatus.PROCESSING
        assert competitor.blog_post_generation_started_at is not None
        assert queued == {
            "task_name": "core.tasks.generate_competitor_vs_blog_post",
            "competitor_id": competitor.id,
            "group": "Generate Competitor VS Blog Post",
        }

    def test_generate_endpoint_does_not_double_queue_when_processing(self, client, monkeypatch):
        user = User.objects.create_user(
            username="competitor-user-2",
            email="competitor-user-2@example.com",
            password="secret",
        )
        project = Project.objects.create(
            profile=user.profile,
            name="Beta",
            url="https://beta.test",
        )
        competitor = Competitor.objects.create(
            project=project,
            name="Gamma",
            url="https://gamma.test",
            description="Competing product",
            blog_post_generation_status=CompetitorPostGenerationStatus.PROCESSING,
        )

        async_calls = {"count": 0}

        def fake_async_task(*_args, **_kwargs):
            async_calls["count"] += 1
            return "task-123"

        monkeypatch.setattr("core.api.views.async_task", fake_async_task)
        monkeypatch.setattr("core.api.views.get_verified_email_gate_error", lambda *_args, **_kwargs: None)

        client.force_login(user)
        response = client.post(
            "/api/generate-competitor-vs-title",
            data=json.dumps({"competitor_id": competitor.id}),
            content_type="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "processing"
        assert "already being generated" in payload["message"]
        assert async_calls["count"] == 0

    def test_status_endpoint_returns_completed_with_view_url(self, client):
        user = User.objects.create_user(
            username="competitor-user-3",
            email="competitor-user-3@example.com",
            password="secret",
        )
        project = Project.objects.create(
            profile=user.profile,
            name="Delta",
            url="https://delta.test",
        )
        competitor = Competitor.objects.create(
            project=project,
            name="Epsilon",
            url="https://epsilon.test",
            description="Competing product",
            blog_post="# Ready post",
            blog_post_generation_status=CompetitorPostGenerationStatus.PROCESSING,
        )

        client.force_login(user)
        response = client.get(f"/api/competitor-post-generation-status/{competitor.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["view_post_url"] == f"/project/{project.id}/competitor/{competitor.id}/post/"

        competitor.refresh_from_db()
        assert competitor.blog_post_generation_status == CompetitorPostGenerationStatus.COMPLETED
