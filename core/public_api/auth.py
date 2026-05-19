from django.http import HttpRequest
from ninja.security import APIKeyHeader

from core.models import Profile
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


def _redact_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:2]}***{key[-2:]}"


class PublicAPIKeyAuth(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str) -> Profile | None:
        try:
            return Profile.objects.get(key=key)
        except Profile.DoesNotExist:
            logger.warning("[Public API Auth] Invalid API key", key=_redact_key(key))
            return None


public_api_key_auth = PublicAPIKeyAuth()
