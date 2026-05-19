import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory

from core.api.auth import APIKeyAuth, SessionAuth, SuperuserAPIKeyAuth


@pytest.fixture
def request_factory():
    return RequestFactory()


@pytest.mark.django_db
class TestApiKeyAuth:
    def test_returns_profile_for_valid_key(self):
        user = User.objects.create_user(
            username="api-key-user",
            email="api-key-user@example.com",
            password="secret",
        )
        profile = user.profile

        authenticated_profile = APIKeyAuth().authenticate(request=None, key=profile.key)

        assert authenticated_profile.id == profile.id

    def test_returns_none_for_invalid_key(self):
        authenticated_profile = APIKeyAuth().authenticate(request=None, key="invalid")

        assert authenticated_profile is None


@pytest.mark.django_db
class TestSessionAuth:
    def test_returns_profile_for_authenticated_user(self, request_factory):
        user = User.objects.create_user(
            username="session-user",
            email="session-user@example.com",
            password="secret",
        )

        request = request_factory.get("/api")
        request.user = user

        authenticated_profile = SessionAuth().authenticate(request)

        assert authenticated_profile.id == user.profile.id

    def test_returns_none_for_anonymous_user(self, request_factory):
        request = request_factory.get("/api")
        request.user = AnonymousUser()

        authenticated_profile = SessionAuth().authenticate(request)

        assert authenticated_profile is None

    def test_returns_none_when_authenticated_user_has_no_profile(self, request_factory):
        user = User.objects.create_user(
            username="no-profile-user",
            email="no-profile-user@example.com",
            password="secret",
        )
        user.profile.delete()
        user_without_profile = User.objects.get(id=user.id)

        request = request_factory.get("/api")
        request.user = user_without_profile

        authenticated_profile = SessionAuth().authenticate(request)

        assert authenticated_profile is None


@pytest.mark.django_db
class TestSuperuserApiKeyAuth:
    def test_returns_profile_for_superuser_with_valid_key(self):
        user = User.objects.create_superuser(
            username="superuser-api-key",
            email="superuser-api-key@example.com",
            password="secret",
        )
        profile = user.profile

        authenticated_profile = SuperuserAPIKeyAuth().authenticate(request=None, key=profile.key)

        assert authenticated_profile.id == profile.id

    def test_returns_none_for_non_superuser_with_valid_key(self):
        user = User.objects.create_user(
            username="regular-user",
            email="regular-user@example.com",
            password="secret",
        )

        authenticated_profile = SuperuserAPIKeyAuth().authenticate(
            request=None,
            key=user.profile.key,
        )

        assert authenticated_profile is None

    def test_returns_none_for_invalid_key(self):
        authenticated_profile = SuperuserAPIKeyAuth().authenticate(request=None, key="invalid")

        assert authenticated_profile is None
