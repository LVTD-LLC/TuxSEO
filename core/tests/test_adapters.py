import re
from unittest.mock import Mock, patch

from django.contrib.auth.models import User

from core.adapters import CustomAccountAdapter


def test_populate_username_generates_unique_username_from_email():
    user = User(email="john@example.com", username="")

    adapter = CustomAccountAdapter()
    with patch("core.adapters.User.objects.filter") as mock_filter:
        mock_filter.side_effect = [
            Mock(exists=Mock(return_value=True)),
            Mock(exists=Mock(return_value=False)),
        ]
        adapter.populate_username(request=None, user=user)

    assert user.username == "john1"


def test_populate_username_uses_fallback_when_email_prefix_is_empty():
    user = User(email="@example.com", username="")

    adapter = CustomAccountAdapter()
    with patch("core.adapters.User.objects.filter") as mock_filter:
        mock_filter.return_value.exists.return_value = False
        adapter.populate_username(request=None, user=user)

    assert re.fullmatch(r"user[0-9a-f]{8}", user.username)


def test_populate_username_does_not_overwrite_existing_username():
    user = User(email="person@example.com", username="kept-name")

    adapter = CustomAccountAdapter()
    with patch("core.adapters.User.objects.filter") as mock_filter:
        adapter.populate_username(request=None, user=user)

    assert user.username == "kept-name"
    mock_filter.assert_not_called()
