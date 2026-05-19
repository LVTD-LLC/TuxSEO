from django.http import HttpRequest
from ninja.security import APIKeyQuery

from core.models import Profile
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


def _redact_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:2]}***{key[-2:]}"


class APIKeyAuth(APIKeyQuery):
    param_name = "api_key"

    def authenticate(self, request: HttpRequest, key: str) -> Profile | None:
        logger.info(
            "[Django Ninja Auth] API Request with key",
            key=_redact_key(key),
        )
        try:
            return Profile.objects.get(key=key)
        except Profile.DoesNotExist:
            logger.warning("[Django Ninja Auth] Invalid API key", key=_redact_key(key))
            return None


class SessionAuth:
    """Authentication via Django session"""

    def authenticate(self, request: HttpRequest) -> Profile | None:
        if hasattr(request, "user") and request.user.is_authenticated:
            logger.info(
                "[Django Ninja Auth] API Request with authenticated user",
                user_id=request.user.id,
            )
            try:
                return request.user.profile
            except Profile.DoesNotExist:
                logger.warning("[Django Ninja Auth] No profile for user", user_id=request.user.id)
                return None
        return None

    def __call__(self, request: HttpRequest):
        return self.authenticate(request)


class SuperuserAPIKeyAuth(APIKeyQuery):
    param_name = "api_key"

    def authenticate(self, request: HttpRequest, key: str) -> Profile | None:
        try:
            profile = Profile.objects.get(key=key)
            if profile.user.is_superuser:
                return profile
            logger.warning(
                "[Django Ninja Auth] Non-superuser attempted admin access",
                profile_id=profile.user.id,
            )
            return None
        except Profile.DoesNotExist:
            logger.warning("[Django Ninja Auth] Profile does not exist", key=_redact_key(key))
            return None


api_key_auth = APIKeyAuth()
session_auth = SessionAuth()
superuser_api_auth = SuperuserAPIKeyAuth()
