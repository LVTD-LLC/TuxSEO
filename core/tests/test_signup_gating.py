from pathlib import Path

from django.test import RequestFactory, override_settings

from core.adapters import CustomAccountAdapter, CustomSocialAccountAdapter


@override_settings(ALLOW_SIGNUPS=True)
def test_account_signup_adapter_defaults_open_for_signups():
    request = RequestFactory().get("/accounts/signup/")

    assert CustomAccountAdapter().is_open_for_signup(request) is True


@override_settings(ALLOW_SIGNUPS=False)
def test_account_signup_adapter_can_close_new_signups():
    request = RequestFactory().get("/accounts/signup/")

    assert CustomAccountAdapter().is_open_for_signup(request) is False


@override_settings(ALLOW_SIGNUPS=False)
def test_social_signup_adapter_uses_same_signup_gate():
    request = RequestFactory().get("/accounts/google/login/callback/")

    assert CustomSocialAccountAdapter().is_open_for_signup(request, sociallogin=None) is False


def test_signup_closed_template_tells_existing_users_to_log_in():
    template_path = (
        Path(__file__).resolve().parents[2] / "frontend/templates/account/signup_closed.html"
    )

    content = template_path.read_text(encoding="utf-8")

    assert "Signups paused" in content
    assert "Existing users can continue using the product as usual" in content
    assert "account_login" in content
    assert 'name="email"' not in content
    assert 'name="password1"' not in content
