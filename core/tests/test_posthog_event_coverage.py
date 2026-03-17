import inspect
from unittest.mock import Mock, patch

from django.test import override_settings

from core.analytics import ANALYTICS_EVENTS, EVENT_TAXONOMY
from core.tasks import track_event
from core.api import views as api_views
from core import signals, views


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


def test_instrumentation_is_wired_for_critical_server_flows():
    assert "ANALYTICS_EVENTS.LOGIN_SUCCEEDED" in inspect.getsource(signals.capture_login_succeeded)
    assert "ANALYTICS_EVENTS.INTEGRATION_CONNECTED" in inspect.getsource(
        views.ProjectIntegrationsGoogleCallbackView._save_google_integration
    )
    assert "ANALYTICS_EVENTS.KEYWORD_UPDATED" in inspect.getsource(api_views.add_keyword_to_project)
    assert "ANALYTICS_EVENTS.TITLE_GENERATION_COMPLETED" in inspect.getsource(
        api_views.generate_title_suggestions
    )
    assert "ANALYTICS_EVENTS.PUBLISH_SUCCEEDED" in inspect.getsource(
        api_views.post_generated_blog_post
    )
