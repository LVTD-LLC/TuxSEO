from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, override_settings

from core.context_processors import (
    available_social_providers,
    posthog_api_key,
    pro_subscription_status,
    referrer_banner,
    turnstile_site_key,
)
from core.models import ReferrerBanner


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.mark.django_db
class TestProSubscriptionStatus:
    def test_returns_false_for_anonymous_user(self, request_factory):
        request = request_factory.get("/")
        request.user = AnonymousUser()

        context = pro_subscription_status(request)

        assert context == {"has_pro_subscription": False}

    def test_returns_true_for_superuser(self, request_factory):
        user = User.objects.create_user(
            username="superuser",
            email="superuser@example.com",
            password="testpass123",
            is_superuser=True,
        )
        request = request_factory.get("/")
        request.user = user

        context = pro_subscription_status(request)

        assert context == {"has_pro_subscription": True}


class TestPosthogApiKeyContextProcessor:
    @override_settings(POSTHOG_API_KEY="phc_test_key")
    def test_returns_posthog_api_key_from_settings(self, request_factory):
        request = request_factory.get("/")

        context = posthog_api_key(request)

        assert context == {"posthog_api_key": "phc_test_key"}


class TestAvailableSocialProviders:
    @override_settings(SOCIALACCOUNT_PROVIDERS={"github": {}, "google": {}})
    def test_merges_configured_and_database_providers(self, request_factory):
        request = request_factory.get("/")

        with patch("core.context_processors.SocialApp.objects.all") as mock_social_apps:
            mock_social_apps.return_value = [
                SimpleNamespace(provider="apple"),
                SimpleNamespace(provider="github"),
            ]

            context = available_social_providers(request)

        assert context["available_social_providers"] == ["apple", "github", "google"]
        assert context["has_social_providers"] is True

    @override_settings(SOCIALACCOUNT_PROVIDERS={"github": {}})
    def test_handles_social_app_lookup_errors_gracefully(self, request_factory):
        request = request_factory.get("/")

        with patch(
            "core.context_processors.SocialApp.objects.all", side_effect=Exception("db unavailable")
        ):
            context = available_social_providers(request)

        assert context["available_social_providers"] == ["github"]
        assert context["has_social_providers"] is True


class TestTurnstileSiteKey:
    @override_settings(CLOUDFLARE_TURNSTILE_SITEKEY="turnstile_test_key")
    def test_returns_turnstile_site_key(self, request_factory):
        request = request_factory.get("/")

        context = turnstile_site_key(request)

        assert context == {"turnstile_site_key": "turnstile_test_key"}

    @override_settings(CLOUDFLARE_TURNSTILE_SITEKEY="")
    def test_falls_back_to_legacy_site_key_env(self, request_factory, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_TURNSTILE_SITE_KEY", "legacy-site-key")
        request = request_factory.get("/")

        context = turnstile_site_key(request)

        assert context == {"turnstile_site_key": "legacy-site-key"}


@pytest.mark.django_db
class TestReferrerBannerContextProcessor:
    def test_returns_exact_referrer_match_banner(self, request_factory):
        matching_banner = ReferrerBanner.objects.create(
            referrer="producthunt",
            referrer_printable_name="Product Hunt",
            is_active=True,
        )

        request = request_factory.get("/?ref=producthunt")

        context = referrer_banner(request)

        assert context["referrer_banner"].id == matching_banner.id

    def test_returns_fallback_black_friday_banner(self, request_factory):
        fallback_banner = ReferrerBanner.objects.create(
            referrer="black-friday",
            referrer_printable_name="Black Friday Special",
            is_active=True,
        )

        request = request_factory.get("/")

        context = referrer_banner(request)

        assert context["referrer_banner"].id == fallback_banner.id

    def test_returns_empty_context_when_no_banner_applies(self, request_factory):
        request = request_factory.get("/?ref=unknown-source")

        context = referrer_banner(request)

        assert context == {}
