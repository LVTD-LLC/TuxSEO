from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings

from core.api.views import get_api_key, regenerate_api_key


def test_settings_template_contains_api_access_controls():
    template_path = Path(settings.BASE_DIR) / "frontend" / "templates" / "pages" / "user-settings.html"
    template_content = template_path.read_text(encoding="utf-8")

    assert "API Access" in template_content
    assert "data-controller=\"api-key\"" in template_content
    assert "data-api-key-fetch-url-value=\"/api/settings/api-key\"" in template_content
    assert "data-api-key-regenerate-url-value=\"/api/settings/api-key/regenerate\"" in template_content
    assert "Reveal" in template_content
    assert "Copy" in template_content
    assert "Regenerate API Key" in template_content
    assert "value=\"**********\"" in template_content


def test_get_api_key_returns_authenticated_users_key():
    request = SimpleNamespace(auth=SimpleNamespace(key="api-key-123"))

    response = get_api_key(request)

    assert response == {"status": "success", "key": "api-key-123"}


def test_regenerate_api_key_updates_profile_key_and_saves():
    profile = SimpleNamespace(key="old-key", id=1, user_id=7, save=Mock())
    request = SimpleNamespace(auth=profile)

    filter_result = Mock()
    filter_result.exists.side_effect = [True, False]

    with (
        patch("core.api.views.generate_random_key", side_effect=["old-key", "taken-key", "new-key"]),
        patch("core.api.views.Profile.objects.filter", return_value=filter_result),
    ):
        response = regenerate_api_key(request)

    assert response == {"status": "success", "key": "new-key"}
    assert profile.key == "new-key"
    profile.save.assert_called_once_with(update_fields=["key"])


def test_regenerate_api_key_returns_error_when_unique_key_not_found():
    profile = SimpleNamespace(key="fixed-key", id=1, user_id=7, save=Mock())
    request = SimpleNamespace(auth=profile)

    with patch("core.api.views.generate_random_key", return_value="fixed-key"):
        status_code, response = regenerate_api_key(request)

    assert status_code == 500
    assert response == {
        "status": "error",
        "key": "",
        "message": "Failed to regenerate API key. Please try again.",
    }
    profile.save.assert_not_called()
