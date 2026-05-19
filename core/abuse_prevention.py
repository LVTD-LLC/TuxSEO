from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.cache import cache

from django_q.tasks import async_task

from core.analytics import ANALYTICS_EVENTS
from core.tasks import track_event
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

DEFAULT_DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "temp-mail.org",
    "tempmail.com",
    "yopmail.com",
}


def get_request_ip_address(request) -> str:
    forwarded_for_header = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for_header:
        forwarded_for_ip_list = [ip.strip() for ip in forwarded_for_header.split(",") if ip.strip()]
        if forwarded_for_ip_list:
            return forwarded_for_ip_list[0]

    return request.META.get("REMOTE_ADDR", "")


def is_signup_rate_limited(ip_address: str) -> bool:
    if not ip_address:
        return False

    max_attempts = settings.SIGNUP_RATE_LIMIT_ATTEMPTS_PER_IP
    window_seconds = settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS
    cache_key = f"signup-rate-limit:{ip_address}"

    current_attempt_count = cache.get(cache_key, 0)
    if current_attempt_count >= max_attempts:
        return True

    if current_attempt_count == 0:
        cache.set(cache_key, 1, timeout=window_seconds)
    else:
        cache.incr(cache_key)

    return False


def is_disposable_email_domain(email_address: str) -> bool:
    if "@" not in email_address:
        return False

    domain = email_address.rsplit("@", 1)[1].strip().lower()

    configured_blocklist = settings.SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST
    blocked_domains = set(configured_blocklist) if configured_blocklist else set()
    blocked_domains.update(DEFAULT_DISPOSABLE_EMAIL_DOMAINS)

    return domain in blocked_domains


def has_verified_email(profile) -> bool:
    return EmailAddress.objects.filter(
        user=profile.user,
        email=profile.user.email,
        verified=True,
    ).exists()


def get_verified_email_required_api_response(action_name: str) -> dict:
    return {
        "status": "error",
        "message": f"Please verify your email before using {action_name}. Check your inbox or resend the confirmation email from Settings.",
    }


def enforce_verified_email_for_expensive_action(profile, action_name: str) -> dict | None:
    if not settings.REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS:
        return None

    if has_verified_email(profile):
        return None

    logger.warning(
        "[VerifiedEmailGate] Blocked unverified user from expensive action",
        profile_id=profile.id,
        user_id=profile.user.id,
        action_name=action_name,
    )

    try:
        async_task(
            track_event,
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.ABUSE_GUARDRAIL_TRIGGERED,
            properties={
                "guardrail_reason": "unverified_email",
                "guardrail_action": action_name,
            },
            source_function="core.abuse_prevention.enforce_verified_email_for_expensive_action",
            group="Track Event",
        )
    except Exception as error:
        logger.warning(
            "[VerifiedEmailGate] Failed to emit guardrail analytics event",
            profile_id=profile.id,
            action_name=action_name,
            error=str(error),
        )

    return get_verified_email_required_api_response(action_name)
