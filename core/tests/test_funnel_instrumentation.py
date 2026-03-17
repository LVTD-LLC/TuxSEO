import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.contrib.auth.models import User
from django.urls import reverse

from core.analytics import ANALYTICS_EVENTS
from core.models import BlogPostTitleSuggestion, Profile
from core.webhooks import handle_checkout_completed


@pytest.mark.django_db
def test_signup_page_instruments_signup_started_event(client):
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert ANALYTICS_EVENTS.SIGNUP_STARTED in content
    assert "window.posthog.capture" in content
    assert 'name="email"' in content
    assert 'name="password1"' in content
    assert 'name="username"' not in content
    assert 'name="password2"' not in content


@pytest.mark.django_db
def test_email_confirmation_emits_email_verified_event(client):
    user = User.objects.create_user(
        username="confirm-funnel-user",
        email="confirm-funnel@example.com",
        password="secret",
    )
    email_address = EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=False,
    )
    email_confirmation = EmailConfirmationHMAC.create(email_address)

    with patch("core.signals.async_task") as mock_async_task:
        response = client.get(reverse("account_confirm_email", args=[email_confirmation.key]))

    assert response.status_code == 302

    track_event_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args and call.args[0] == "core.tasks.track_event"
    ]

    assert len(track_event_calls) == 1
    assert track_event_calls[0].kwargs["event_name"] == ANALYTICS_EVENTS.EMAIL_VERIFIED
    assert track_event_calls[0].kwargs["properties"]["email_domain"] == "example.com"


@pytest.mark.django_db
def test_create_project_emits_project_create_succeeded_event():
    user = User.objects.create_user(
        username="project-created-user",
        email="project-created@example.com",
        password="secret",
    )
    profile = user.profile

    with patch("core.models.async_task") as mock_async_task:
        profile.get_or_create_project(
            url="https://project-created.example.com",
            source="unit-test",
        )

    track_event_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args and call.args[0] == "core.tasks.track_event"
    ]

    assert len(track_event_calls) == 2

    emitted_event_names = {call.kwargs["event_name"] for call in track_event_calls}
    assert ANALYTICS_EVENTS.PROJECT_CREATE_SUCCEEDED in emitted_event_names
    assert ANALYTICS_EVENTS.ONBOARDING_COMPLETED in emitted_event_names

    assert track_event_calls[0].kwargs["properties"]["source"] == "unit-test"


@pytest.mark.django_db
def test_create_checkout_session_emits_checkout_started_event(client):
    user = User.objects.create_user(
        username="checkout-start-user",
        email="checkout-start@example.com",
        password="secret",
    )
    client.force_login(user)

    fake_price = SimpleNamespace(id="price_test")
    fake_customer = SimpleNamespace(id="cus_test")
    fake_checkout_session = SimpleNamespace(
        id="cs_test",
        url="https://checkout.stripe.test/session/cs_test",
    )

    with patch("core.views.get_price_for_product_name", return_value=fake_price):
        with patch(
            "core.views.djstripe_models.Customer.get_or_create",
            return_value=(fake_customer, True),
        ):
            with patch(
                "core.views.stripe.checkout.Session.create",
                return_value=fake_checkout_session,
            ):
                with patch("core.views.async_task") as mock_async_task:
                    response = client.get(
                        reverse("user_upgrade_checkout_session", kwargs={"product_name": "Pro"})
                    )

    assert response.status_code == 303
    assert response.url == fake_checkout_session.url

    track_event_calls = [
        call
        for call in mock_async_task.call_args_list
        if call.args and call.args[0].__name__ == "track_event"
    ]

    assert len(track_event_calls) == 1
    assert track_event_calls[0].kwargs["event_name"] == ANALYTICS_EVENTS.CHECKOUT_STARTED
    assert track_event_calls[0].kwargs["properties"]["product_name"] == "Pro"
    assert track_event_calls[0].kwargs["properties"]["price_id"] == fake_price.id


def test_checkout_completed_webhook_emits_checkout_succeeded_event():
    fake_event = Mock()
    fake_event.data.get.side_effect = lambda key, default=None: {
        "object": {
            "id": "cs_webhook_123",
            "customer": "cus_123",
            "mode": "subscription",
            "payment_status": "paid",
            "subscription": "sub_123",
        }
    }.get(key, default)
    fake_event.id = "evt_123"

    fake_profile = Mock(id=123)

    with patch("core.webhooks.Profile.objects.filter") as mock_profile_filter:
        mock_profile_filter.return_value.first.return_value = fake_profile
        with patch("core.webhooks.async_task") as mock_async_task:
            handle_checkout_completed(event=fake_event)

    assert mock_async_task.call_count == 1
    assert mock_async_task.call_args.kwargs["event_name"] == ANALYTICS_EVENTS.CHECKOUT_SUCCEEDED
    assert mock_async_task.call_args.kwargs["properties"]["checkout_id"] == "cs_webhook_123"


def test_blog_generation_uses_canonical_first_blog_generated_event():
    source = inspect.getsource(BlogPostTitleSuggestion.generate_content)
    assert "ANALYTICS_EVENTS.FIRST_BLOG_GENERATED" in source
    assert "core.tasks.track_event" in source


def test_track_event_uses_profile_model_for_queryable_funnel_dimensions():
    source = inspect.getsource(Profile.get_or_create_project)
    assert "profile_email" in source
