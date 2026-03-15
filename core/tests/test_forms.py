import json
from unittest.mock import Mock, patch

import pytest
import requests
from django import forms
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from core.forms import (
    AutoSubmissionSettingForm,
    CustomSignUpForm,
    ProfileUpdateForm,
    TURNSTILE_REASON_PROVIDER_ERROR,
    TURNSTILE_REASON_TOKEN_EXPIRED,
    TURNSTILE_REASON_TOKEN_INVALID,
)


class TestAutoSubmissionSettingForm:
    def test_clean_body_returns_dict_as_is(self):
        form = AutoSubmissionSettingForm()
        form.cleaned_data = {"body": {"title": "hello"}}

        cleaned_body = form.clean_body()

        assert cleaned_body == {"title": "hello"}

    def test_clean_body_parses_json_string_to_dict(self):
        form = AutoSubmissionSettingForm()
        form.cleaned_data = {"body": '{"title": "hello"}'}

        cleaned_body = form.clean_body()

        assert cleaned_body == {"title": "hello"}

    def test_clean_body_returns_empty_dict_for_empty_string(self):
        form = AutoSubmissionSettingForm()
        form.cleaned_data = {"body": ""}

        cleaned_body = form.clean_body()

        assert cleaned_body == {}

    def test_clean_body_raises_error_for_invalid_json(self):
        form = AutoSubmissionSettingForm()
        form.cleaned_data = {"body": "{invalid-json}"}

        with pytest.raises(json.JSONDecodeError):
            form.clean_body()


class TestCustomSignUpFormTurnstile:
    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key")
    @patch("core.forms.requests.post")
    def test_verify_turnstile_token_returns_success_payload_on_success(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {"success": True}
        mock_post.return_value = mock_response

        form = CustomSignUpForm()

        verification_result = form._verify_turnstile_token("test-token")

        assert verification_result["success"] is True
        assert verification_result["reason_code"] == "verified"

    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key")
    @patch("core.forms.requests.post")
    def test_verify_turnstile_token_maps_invalid_response_error(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": False,
            "error-codes": ["invalid-input-response"],
        }
        mock_post.return_value = mock_response

        form = CustomSignUpForm()

        verification_result = form._verify_turnstile_token("test-token")

        assert verification_result["success"] is False
        assert verification_result["reason_code"] == TURNSTILE_REASON_TOKEN_INVALID

    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key")
    @patch("core.forms.requests.post")
    def test_verify_turnstile_token_maps_timeout_duplicate_error(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": False,
            "error-codes": ["timeout-or-duplicate"],
        }
        mock_post.return_value = mock_response

        form = CustomSignUpForm()

        verification_result = form._verify_turnstile_token("test-token")

        assert verification_result["success"] is False
        assert verification_result["reason_code"] == TURNSTILE_REASON_TOKEN_EXPIRED

    @override_settings(CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key")
    @patch("core.forms.requests.post", side_effect=requests.RequestException("network error"))
    def test_verify_turnstile_token_maps_request_exception_to_provider_error(self, mock_post):
        form = CustomSignUpForm()

        verification_result = form._verify_turnstile_token("test-token")

        assert verification_result["success"] is False
        assert verification_result["reason_code"] == TURNSTILE_REASON_PROVIDER_ERROR

    @override_settings(
        CLOUDFLARE_TURNSTILE_SITEKEY="site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="",
    )
    @patch("allauth.account.forms.SignupForm.clean", return_value={})
    def test_clean_requires_turnstile_token_when_site_key_is_configured(self, mock_signup_clean):
        form = CustomSignUpForm(data={})
        form.request = RequestFactory().post("/accounts/signup/")

        with pytest.raises(
            forms.ValidationError, match="Please complete the verification challenge"
        ):
            form.clean()

    @override_settings(
        CLOUDFLARE_TURNSTILE_SITEKEY="site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key",
    )
    @patch("allauth.account.forms.SignupForm.clean", return_value={"email": "test@example.com"})
    @patch.object(
        CustomSignUpForm,
        "_verify_turnstile_token",
        return_value={"success": True, "reason_code": "verified", "error_codes": []},
    )
    def test_clean_passes_remote_ip_to_turnstile_verification(
        self, mock_verify_turnstile_token, mock_signup_clean
    ):
        remote_ip = "203.0.113.10"
        form = CustomSignUpForm(data={"cf-turnstile-response": "test-token"})
        form.request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR=remote_ip)

        cleaned_data = form.clean()

        assert cleaned_data == {"email": "test@example.com"}
        mock_verify_turnstile_token.assert_called_once_with("test-token", remote_ip)

    @override_settings(
        CLOUDFLARE_TURNSTILE_SITEKEY="site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key",
    )
    @patch("allauth.account.forms.SignupForm.clean", return_value={"email": "test@example.com"})
    @patch.object(
        CustomSignUpForm,
        "_verify_turnstile_token",
        return_value={
            "success": False,
            "reason_code": TURNSTILE_REASON_TOKEN_EXPIRED,
            "error_codes": ["timeout-or-duplicate"],
        },
    )
    def test_clean_returns_actionable_message_when_turnstile_token_expired(
        self, mock_verify_turnstile_token, mock_signup_clean
    ):
        form = CustomSignUpForm(data={"cf-turnstile-response": "stale-token"})
        form.request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR="198.51.100.7")

        with pytest.raises(forms.ValidationError, match="Verification expired"):
            form.clean()

    @override_settings(
        CLOUDFLARE_TURNSTILE_SITEKEY="site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="secret-key",
    )
    @patch(
        "allauth.account.forms.SignupForm.clean",
        return_value={
            "email": "verified@example.com",
            "password1": "SafePass123!",
        },
    )
    @patch.object(
        CustomSignUpForm,
        "_verify_turnstile_token",
        return_value={"success": True, "reason_code": "verified", "error_codes": []},
    )
    def test_verified_signup_happy_path_returns_cleaned_data_without_errors(
        self, mock_verify_turnstile_token, mock_signup_clean
    ):
        """Regression guard for verified-signup happy path."""

        form = CustomSignUpForm(
            data={
                "email": "verified@example.com",
                "password1": "SafePass123!",
                "cf-turnstile-response": "valid-token",
            }
        )
        form.request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR="198.51.100.8")

        cleaned_data = form.clean()

        assert cleaned_data["email"] == "verified@example.com"
        assert cleaned_data["password1"] == "SafePass123!"

    @override_settings(
        SIGNUP_RATE_LIMIT_ATTEMPTS_PER_IP=1,
        SIGNUP_RATE_LIMIT_WINDOW_SECONDS=60,
        CLOUDFLARE_TURNSTILE_SITEKEY="",
    )
    @patch("allauth.account.forms.SignupForm.clean", return_value={"email": "person@example.com"})
    def test_clean_blocks_when_signup_rate_limit_is_exceeded(self, mock_signup_clean):
        cache.clear()
        form = CustomSignUpForm(data={})
        form.request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR="198.51.100.10")

        first_cleaned_data = form.clean()
        assert first_cleaned_data == {"email": "person@example.com"}

        with pytest.raises(forms.ValidationError, match="Too many signup attempts"):
            form.clean()

    @override_settings(
        CLOUDFLARE_TURNSTILE_SITEKEY="",
        SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST=["mailinator.com"],
    )
    @patch("allauth.account.forms.SignupForm.clean", return_value={"email": "bot@mailinator.com"})
    def test_clean_blocks_disposable_email_domains(self, mock_signup_clean):
        form = CustomSignUpForm(data={})
        form.request = RequestFactory().post("/accounts/signup/", REMOTE_ADDR="198.51.100.11")

        with pytest.raises(forms.ValidationError, match="permanent email address"):
            form.clean()


@pytest.mark.django_db
class TestProfileUpdateForm:
    def test_save_updates_user_fields_from_form_data(self):
        user = User.objects.create_user(
            username="profile-user",
            email="before@example.com",
            password="secret",
            first_name="Before",
            last_name="Name",
        )

        profile = user.profile

        form = ProfileUpdateForm(
            data={
                "first_name": "After",
                "last_name": "Updated",
                "email": "after@example.com",
            },
            instance=profile,
        )

        assert form.is_valid()

        updated_profile = form.save()

        user.refresh_from_db()
        assert updated_profile.id == profile.id
        assert user.first_name == "After"
        assert user.last_name == "Updated"
        assert user.email == "after@example.com"
