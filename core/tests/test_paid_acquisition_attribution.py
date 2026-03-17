from unittest.mock import patch


import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from core.acquisition import ATTRIBUTION_SESSION_KEY, capture_request_attribution
from core.analytics import ANALYTICS_EVENTS
from core.tasks import track_event


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_includes_latest_touch_acquisition_properties():
    user = User.objects.create_user(
        username="attribution-user",
        email="attribution-user@example.com",
        password="secret",
    )
    profile = user.profile

    profile.first_touch_attribution = {
        "channel": "meta",
        "utm_source": "facebook",
        "copy_variant": "A",
        "timestamp": "2026-03-17T10:00:00+00:00",
    }
    profile.latest_touch_attribution = {
        "channel": "google",
        "platform": "google_ads",
        "utm_source": "google",
        "utm_campaign": "spring-search",
        "copy_variant": "B",
        "gclid": "test-gclid-123",
        "landing_page": "/pricing",
        "timestamp": "2026-03-17T11:00:00+00:00",
    }
    profile.save(update_fields=["first_touch_attribution", "latest_touch_attribution"])

    with patch("core.tasks.posthog.capture") as capture_mock:
        result = track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.SIGNUP_COMPLETED,
            properties={"source": "unit-test"},
        )

    assert "Tracked event signup_completed" in result
    capture_mock.assert_called_once()

    sent_properties = capture_mock.call_args.kwargs["properties"]
    assert sent_properties["acquisition_schema_version"] == 1
    assert sent_properties["channel"] == "google"
    assert sent_properties["utm_source"] == "google"
    assert sent_properties["copy_variant"] == "B"
    assert sent_properties["gclid"] == "test-gclid-123"
    assert sent_properties["first_touch_channel"] == "meta"
    assert sent_properties["latest_touch_platform"] == "google_ads"


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_signup_view_persists_attribution_from_session(client):
    landing_url = reverse("account_signup") + "?utm_source=google&utm_campaign=spring&copy_variant=A"
    response = client.get(landing_url)
    assert response.status_code == 200

    session = client.session
    assert ATTRIBUTION_SESSION_KEY in session

    with patch("core.views.async_task") as async_task_mock:
        response = client.post(
            reverse("account_signup"),
            {
                "email": "new-attribution-user@example.com",
                "password1": "ComplexPwd123!",
                "remember": "true",
            },
            follow=True,
        )

    assert response.status_code == 200

    created_user = User.objects.get(email="new-attribution-user@example.com")
    profile = created_user.profile

    assert profile.first_touch_attribution["utm_source"] == "google"
    assert profile.latest_touch_attribution["copy_variant"] == "A"

    track_event_calls = [
        call
        for call in async_task_mock.call_args_list
        if call.args
        and (
            call.args[0] == "core.tasks.track_event"
            or getattr(call.args[0], "__name__", "") == "track_event"
        )
    ]
    assert track_event_calls


@pytest.mark.django_db
def test_capture_request_attribution_ignores_email_like_params(rf):
    class DummySession(dict):
        modified = False

    request = rf.get("/pricing/?utm_source=google&copy_variant=A&campaign_name=has_email@test.com")
    request.session = DummySession()

    snapshot = capture_request_attribution(request)

    assert snapshot is not None
    assert "campaign_name" not in snapshot.latest_touch
    assert snapshot.latest_touch["copy_variant"] == "A"
