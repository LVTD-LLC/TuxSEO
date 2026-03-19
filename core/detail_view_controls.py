from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

MODULE_SEO_ANALYSIS = "seo_analysis"
MODULE_BACKLINK_DISCOVERY = "backlink_discovery"
MODULE_CONTACT_ENRICHMENT = "contact_enrichment"


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    reason: str
    used: int
    limit: int


@dataclass(frozen=True)
class CooldownCheckResult:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    reason: str


def get_detail_view_feature_flags() -> dict[str, bool]:
    return {
        MODULE_SEO_ANALYSIS: bool(
            getattr(settings, "DETAIL_VIEW_SEO_ANALYSIS_ENABLED", True)
        ),
        MODULE_BACKLINK_DISCOVERY: bool(
            getattr(settings, "DETAIL_VIEW_BACKLINK_DISCOVERY_ENABLED", True)
        ),
        MODULE_CONTACT_ENRICHMENT: bool(
            getattr(settings, "DETAIL_VIEW_CONTACT_ENRICHMENT_ENABLED", True)
        ),
    }


def is_module_enabled(module: str) -> bool:
    return get_detail_view_feature_flags().get(module, False)


def _daily_quota_key(*, profile_id: int, module: str, day_key: str) -> str:
    return f"detail-view:quota:{module}:profile:{profile_id}:day:{day_key}"


def _seconds_until_tomorrow_utc() -> int:
    now = timezone.now()
    tomorrow = (now + timezone.timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(int((tomorrow - now).total_seconds()), 60)


def consume_daily_quota(*, profile_id: int, module: str, limit: int) -> QuotaCheckResult:
    if int(limit or 0) <= 0:
        return QuotaCheckResult(allowed=True, reason="unlimited", used=0, limit=0)

    day_key = timezone.now().strftime("%Y%m%d")
    key = _daily_quota_key(profile_id=profile_id, module=module, day_key=day_key)
    ttl_seconds = _seconds_until_tomorrow_utc()

    if cache.add(key, 1, timeout=ttl_seconds):
        return QuotaCheckResult(allowed=True, reason="within_limit", used=1, limit=limit)

    try:
        used = int(cache.incr(key))
    except ValueError:
        # key expired between add/incr paths
        cache.set(key, 1, timeout=ttl_seconds)
        used = 1

    if used <= limit:
        return QuotaCheckResult(allowed=True, reason="within_limit", used=used, limit=limit)

    # best effort rollback to keep enforcement predictable across repeated blocked calls
    try:
        cache.decr(key)
    except ValueError:
        pass

    return QuotaCheckResult(allowed=False, reason="daily_limit", used=limit, limit=limit)


def _cooldown_key(*, profile_id: int, module: str, page_id: int) -> str:
    return f"detail-view:cooldown:{module}:profile:{profile_id}:page:{page_id}"


def consume_cooldown(*, profile_id: int, module: str, page_id: int, cooldown_seconds: int) -> CooldownCheckResult:
    cooldown_seconds = int(cooldown_seconds or 0)
    if cooldown_seconds <= 0:
        return CooldownCheckResult(allowed=True, reason="cooldown_disabled")

    key = _cooldown_key(profile_id=profile_id, module=module, page_id=page_id)
    if cache.add(key, timezone.now().isoformat(), timeout=cooldown_seconds):
        return CooldownCheckResult(allowed=True, reason="cooldown_available")

    return CooldownCheckResult(allowed=False, reason="cooldown")


def consume_action_rate_limit(*, profile_id: int, action: str) -> RateLimitResult:
    max_attempts = int(getattr(settings, "DETAIL_VIEW_ACTION_RATE_LIMIT_ATTEMPTS", 6))
    window_seconds = int(getattr(settings, "DETAIL_VIEW_ACTION_RATE_LIMIT_WINDOW_SECONDS", 60))

    if max_attempts <= 0 or window_seconds <= 0:
        return RateLimitResult(allowed=True, reason="rate_limit_disabled")

    cache_key = f"detail-view:rate-limit:profile:{profile_id}:action:{action}"

    if cache.add(cache_key, 1, timeout=window_seconds):
        return RateLimitResult(allowed=True, reason="within_rate_limit")

    try:
        attempts = int(cache.incr(cache_key))
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        attempts = 1

    if attempts <= max_attempts:
        return RateLimitResult(allowed=True, reason="within_rate_limit")

    try:
        cache.decr(cache_key)
    except ValueError:
        pass

    return RateLimitResult(allowed=False, reason="rate_limited")
