from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.api.views import post_generated_blog_post


def _build_generated_post(post_id: int) -> Mock:
    post = Mock()
    post.id = post_id
    post.project = SimpleNamespace(profile=SimpleNamespace(id=9))
    post.posted = False
    post.date_posted = None
    post.publish_approval_status = "APPROVED"
    post.create_workflow_audit_event = Mock()
    return post


def test_post_generated_blog_post_blocks_when_approval_pending():
    request = SimpleNamespace(auth=SimpleNamespace(id=9))
    generated_post = _build_generated_post(post_id=76)
    generated_post.publish_approval_status = "PENDING"

    with patch("core.api.views.GeneratedBlogPost.objects.filter") as filter_mock:
        filter_mock.return_value.first.return_value = generated_post
        response_data = post_generated_blog_post(request, data=SimpleNamespace(id=generated_post.id))

    assert response_data["status"] == "error"
    assert "approval checkpoint" in response_data["message"]
    generated_post.submit_blog_post_to_endpoint.assert_not_called()


def test_post_generated_blog_post_blocks_when_quality_gate_fails():
    request = SimpleNamespace(auth=SimpleNamespace(id=9))
    generated_post = _build_generated_post(post_id=77)

    with patch("core.api.views.GeneratedBlogPost.objects.filter") as filter_mock:
        filter_mock.return_value.first.return_value = generated_post
        with patch(
            "core.api.views.evaluate_pre_publish_quality_gate",
            return_value={
                "decision": "block",
                "summary": "Generated content is too short.",
                "blocking_checks": [
                    {
                        "severity": "block",
                        "code": "CONTENT_TOO_SHORT",
                        "message": "Generated content is too short.",
                    }
                ],
                "warning_checks": [],
                "checks": [],
                "aggregate_score": 0.4,
            },
        ):
            response_data = post_generated_blog_post(request, data=SimpleNamespace(id=generated_post.id))

    assert response_data["status"] == "error"
    assert response_data["message"].startswith("Publish blocked by quality gate:")
    generated_post.submit_blog_post_to_endpoint.assert_not_called()


def test_post_generated_blog_post_allows_publish_with_warnings():
    request = SimpleNamespace(auth=SimpleNamespace(id=9))
    generated_post = _build_generated_post(post_id=78)
    generated_post.submit_blog_post_to_endpoint.return_value = True

    with patch("core.api.views.GeneratedBlogPost.objects.filter") as filter_mock:
        filter_mock.return_value.first.return_value = generated_post
        with patch(
            "core.api.views.evaluate_pre_publish_quality_gate",
            return_value={
                "decision": "warn",
                "summary": "Quality score is below recommendation.",
                "blocking_checks": [],
                "warning_checks": [
                    {
                        "severity": "warn",
                        "code": "LOW_QUALITY_SCORE",
                        "message": "Quality score is below recommendation.",
                    }
                ],
                "checks": [],
                "aggregate_score": 0.6,
            },
        ):
            response_data = post_generated_blog_post(request, data=SimpleNamespace(id=generated_post.id))

    assert response_data["status"] == "success"
    assert "quality warnings" in response_data["message"]
    generated_post.submit_blog_post_to_endpoint.assert_called_once()
    generated_post.save.assert_called_once()
