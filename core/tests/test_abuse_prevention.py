from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from core.abuse_prevention import (
    enforce_verified_email_for_expensive_action,
    get_request_ip_address,
    is_disposable_email_domain,
    is_signup_rate_limited,
)


class TestGetRequestIpAddress:
    def test_returns_forwarded_for_ip_when_present(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="198.51.100.100",
            HTTP_X_FORWARDED_FOR="203.0.113.1, 203.0.113.2",
        )

        assert get_request_ip_address(request) == "203.0.113.1"

    def test_returns_remote_addr_when_forwarded_for_missing(self):
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.42")

        assert get_request_ip_address(request) == "198.51.100.42"


class TestSignupRateLimit:
    @override_settings(SIGNUP_RATE_LIMIT_ATTEMPTS_PER_IP=2, SIGNUP_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_blocks_after_limit(self):
        cache.clear()
        ip_address = "198.51.100.55"

        assert is_signup_rate_limited(ip_address) is False
        assert is_signup_rate_limited(ip_address) is False
        assert is_signup_rate_limited(ip_address) is True


class TestDisposableEmailBlocklist:
    @override_settings(SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST=["example-disposable.com"])
    def test_returns_true_for_disposable_domain(self):
        assert is_disposable_email_domain("bot@example-disposable.com") is True

    @override_settings(SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST=[])
    def test_returns_false_for_normal_domain(self):
        assert is_disposable_email_domain("person@gmail.com") is False


@pytest.mark.django_db
class TestVerifiedEmailGate:
    @patch("core.abuse_prevention.async_task")
    @override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
    def test_blocks_unverified_users(self, mock_async_task):
        user = User.objects.create_user(
            username="unverified-user",
            email="unverified@example.com",
            password="secret",
        )

        gate_error = enforce_verified_email_for_expensive_action(
            profile=user.profile,
            action_name="blog content generation",
        )

        assert gate_error["status"] == "error"
        assert "verify your email" in gate_error["message"]
        mock_async_task.assert_called_once()

    @patch("core.abuse_prevention.async_task")
    @override_settings(REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=True)
    def test_allows_verified_users(self, mock_async_task):
        user = User.objects.create_user(
            username="verified-user",
            email="verified@example.com",
            password="secret",
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)

        gate_error = enforce_verified_email_for_expensive_action(
            profile=user.profile,
            action_name="blog content generation",
        )

        assert gate_error is None
        mock_async_task.assert_not_called()
