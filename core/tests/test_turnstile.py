from unittest.mock import Mock, patch

from django.test import override_settings

from core.forms import CustomSignUpForm
from core.turnstile import get_turnstile_secret_key, get_turnstile_site_key


class TestTurnstileConfigFallbacks:
    @override_settings(CLOUDFLARE_TURNSTILE_SITEKEY="")
    def test_site_key_falls_back_to_legacy_env_name(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_TURNSTILE_SITE_KEY", "legacy-site-key")

        assert get_turnstile_site_key() == "legacy-site-key"

    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="")
    def test_secret_key_falls_back_to_legacy_env_name(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_TURNSTILE_SECRET", "legacy-secret-key")

        assert get_turnstile_secret_key() == "legacy-secret-key"


class TestTurnstileFormUsesFallbackSecret:
    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="")
    @patch("core.forms.requests.post")
    def test_verify_uses_legacy_secret_env_when_primary_setting_is_empty(
        self, mock_post, monkeypatch
    ):
        monkeypatch.setenv("CLOUDFLARE_TURNSTILE_SECRET", "legacy-secret-key")

        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        form = CustomSignUpForm()

        result = form._verify_turnstile_token("token-value", "203.0.113.55")

        assert result["success"] is True
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["secret"] == "legacy-secret-key"
        assert kwargs["data"]["response"] == "token-value"
        assert kwargs["data"]["remoteip"] == "203.0.113.55"
