from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings

from core.analytics import ANALYTICS_EVENTS, EVENT_TAXONOMY
from core.analytics.tracking import enqueue_track_event
from core.signals import capture_login_succeeded
from core.tasks import track_event


def test_p1_event_coverage_matrix_events_exist_in_taxonomy():
    required_events = {
        "signup_completed",
        "login_succeeded",
        "project_create_succeeded",
        "integration_connected",
        "integration_disconnected",
        "keyword_updated",
        "page_analysis_completed",
        "title_generation_completed",
        "content_generation_succeeded",
        "content_generation_failed",
        "publish_attempted",
        "publish_succeeded",
        "publish_failed",
        "link_exchange_toggled",
        "plan_upgraded",
        "plan_cancelled",
        "detail_view_opened",
        "seo_analysis_run_started",
        "seo_analysis_run_completed",
        "seo_analysis_run_failed",
        "backlink_discovery_started",
        "backlink_discovery_completed",
        "backlink_discovery_failed",
        "opportunities_viewed",
        "contact_method_copied",
        "analytics_page_viewed",
        "analytics_date_range_changed",
        "analytics_refresh_clicked",
        "analytics_source_error_shown",
    }

    taxonomy_events = set(EVENT_TAXONOMY["events"].keys())
    assert required_events.issubset(taxonomy_events)


@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_rejects_missing_required_event_properties():
    fake_user = Mock(email="event-user@example.com")
    fake_profile = Mock(id=1, user=fake_user, state="active", product=None)

    with patch("core.tasks.Profile.objects.get", return_value=fake_profile):
        with patch("core.tasks.posthog.capture") as mock_capture:
            response = track_event(
                profile_id=fake_profile.id,
                event_name=ANALYTICS_EVENTS.PUBLISH_SUCCEEDED,
                properties={"project_id": 1},
            )

    assert "Missing required properties" in response
    mock_capture.assert_not_called()


def test_capture_login_succeeded_enqueues_social_provider_for_callback_paths():
    profile = SimpleNamespace(id=42)
    user = SimpleNamespace(profile=profile)
    request = SimpleNamespace(path="/accounts/google/login/callback/")

    with patch("core.signals.async_task") as async_task_mock:
        capture_login_succeeded(sender=None, request=request, user=user)

    async_task_mock.assert_called_once()
    call_kwargs = async_task_mock.call_args.kwargs
    assert call_kwargs["event_name"] == ANALYTICS_EVENTS.LOGIN_SUCCEEDED
    assert call_kwargs["properties"]["auth_provider"] == "social"
    assert call_kwargs["properties"]["result_status"] == "succeeded"


def test_enqueue_track_event_does_not_raise_when_queue_is_unavailable():
    with patch("core.analytics.tracking.async_task", side_effect=RuntimeError("queue down")):
        enqueue_track_event(
            profile_id=1,
            event_name=ANALYTICS_EVENTS.KEYWORD_UPDATED,
            properties={
                "project_id": 1,
                "keyword_id": 2,
                "update_action": "added",
                "result_status": "succeeded",
            },
            source_function="test",
        )
