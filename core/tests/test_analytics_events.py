import inspect
import json
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from core.analytics import (
    ANALYTICS_EVENT_NAMES,
    ANALYTICS_EVENTS,
    DEPRECATED_ANALYTICS_EVENT_ALIASES,
    EVENT_TAXONOMY_VERSION,
    EVENT_TAXONOMY,
    normalize_event_name,
)
from core.analytics.events import _validate_event_taxonomy
from core.models import Profile
from core.tasks import track_event


def test_event_taxonomy_exposes_expected_canonical_events():
    assert ANALYTICS_EVENTS.SIGNUP_STARTED == "signup_started"
    assert ANALYTICS_EVENTS.SIGNUP_COMPLETED == "signup_completed"
    assert ANALYTICS_EVENTS.EMAIL_VERIFIED == "email_verified"
    assert ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED == "project_create_succeeded"
    assert ANALYTICS_EVENTS.FIRST_BLOG_GENERATED == "first_blog_generated"
    assert ANALYTICS_EVENTS.ONBOARDING_COMPLETED == "onboarding_completed"
    assert ANALYTICS_EVENTS.FIRST_CONTENT_GENERATED == "first_content_generated"
    assert ANALYTICS_EVENTS.CHECKOUT_STARTED == "checkout_started"
    assert ANALYTICS_EVENTS.CHECKOUT_SUCCEEDED == "checkout_succeeded"
    assert ANALYTICS_EVENTS.PROJECT_DELETED == "project_deleted"
    assert ANALYTICS_EVENTS.SIGNUP_COMPLETED in ANALYTICS_EVENT_NAMES


def test_deprecated_aliases_are_explicit_and_normalized():
    assert DEPRECATED_ANALYTICS_EVENT_ALIASES["user_signed_up"] == (
        ANALYTICS_EVENTS.SIGNUP_COMPLETED
    )
    assert DEPRECATED_ANALYTICS_EVENT_ALIASES["project_created"] == (
        ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED
    )
    assert DEPRECATED_ANALYTICS_EVENT_ALIASES["first_post_generated"] == (
        ANALYTICS_EVENTS.FIRST_BLOG_GENERATED
    )
    assert DEPRECATED_ANALYTICS_EVENT_ALIASES["checkout_completed"] == (
        ANALYTICS_EVENTS.CHECKOUT_SUCCEEDED
    )
    assert normalize_event_name("user_signed_up") == ANALYTICS_EVENTS.SIGNUP_COMPLETED
    assert normalize_event_name("project_created") == ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED
    assert normalize_event_name("first_post_generated") == ANALYTICS_EVENTS.FIRST_BLOG_GENERATED
    assert normalize_event_name("checkout_completed") == ANALYTICS_EVENTS.CHECKOUT_SUCCEEDED
    assert (
        normalize_event_name(ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED)
        == ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED
    )


@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_normalizes_deprecated_event_name_before_capture():
    fake_user = Mock(email="event-user@example.com")
    fake_profile = Mock(id=123, user=fake_user, state="active")

    with patch("core.tasks.Profile.objects.get", return_value=fake_profile):
        with patch("core.tasks.posthog.capture") as mock_capture:
            result = track_event(
                profile_id=fake_profile.id,
                event_name="user_signed_up",
                properties={"source": "unit-test"},
            )

    assert "signup_completed" in result
    mock_capture.assert_called_once()

    capture_kwargs = mock_capture.call_args.kwargs
    assert capture_kwargs["event"] == ANALYTICS_EVENTS.SIGNUP_COMPLETED
    assert capture_kwargs["properties"]["event_schema_version"] == EVENT_TAXONOMY_VERSION
    assert capture_kwargs["properties"]["event_stage"] == "activation"


@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_rejects_unknown_event_name():
    fake_user = Mock(email="event-user@example.com")
    fake_profile = Mock(id=123, user=fake_user, state="active")

    with patch("core.tasks.Profile.objects.get", return_value=fake_profile):
        with patch("core.tasks.posthog.capture") as mock_capture:
            result = track_event(
                profile_id=fake_profile.id,
                event_name="not_a_real_event_name",
                properties={"source": "unit-test"},
            )

    assert "Unknown event name" in result
    mock_capture.assert_not_called()


def test_profile_model_uses_canonical_project_created_constant():
    profile_source = inspect.getsource(Profile.get_or_create_project)
    assert "ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED" in profile_source


def test_event_taxonomy_funnel_events_are_unique_and_defined():
    required_funnel_events = {
        "signup_started",
        "signup_completed",
        "email_verified",
        "project_create_succeeded",
        "first_blog_generated",
        "checkout_started",
        "checkout_succeeded",
    }

    event_names = set(EVENT_TAXONOMY["events"].keys())
    assert required_funnel_events.issubset(event_names)
    assert len(ANALYTICS_EVENT_NAMES) == len(set(ANALYTICS_EVENT_NAMES))


def test_validate_event_taxonomy_rejects_malformed_event_names():
    bad_taxonomy = json.loads(json.dumps(EVENT_TAXONOMY))
    bad_taxonomy["events"]["Project Create Succeeded"] = bad_taxonomy["events"].pop(
        "project_create_succeeded"
    )

    with pytest.raises(ValueError, match="malformed event name"):
        _validate_event_taxonomy(bad_taxonomy)


def test_validate_event_taxonomy_rejects_alias_to_unknown_event():
    bad_taxonomy = json.loads(json.dumps(EVENT_TAXONOMY))
    bad_taxonomy["deprecated_aliases"]["legacy_event"] = "missing_event"

    with pytest.raises(ValueError, match="points to unknown event"):
        _validate_event_taxonomy(bad_taxonomy)
