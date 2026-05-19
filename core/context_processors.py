from allauth.socialaccount.models import SocialApp
from django.conf import settings

from core.turnstile import get_turnstile_site_key
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


def pro_subscription_status(request):
    """
    Adds a 'has_pro_subscription' variable to the context.
    This variable is True if the user has an active pro subscription or is a superuser, False otherwise.
    """  # noqa: E501
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        return {"has_pro_subscription": request.user.profile.has_product_or_subscription}
    return {"has_pro_subscription": False}


def posthog_api_key(request):
    return {"posthog_api_key": settings.POSTHOG_API_KEY}


def available_social_providers(request):
    """
    Checks which social authentication providers are available.
    Returns a list of provider names from either SOCIALACCOUNT_PROVIDERS settings
    or SocialApp database entries, as django-allauth supports both configuration methods.
    """
    available_providers = set()

    configured_providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})

    available_providers.update(configured_providers.keys())

    try:
        social_apps = SocialApp.objects.all()
        for social_app in social_apps:
            available_providers.add(social_app.provider)
    except Exception as e:
        logger.warning("Error retrieving SocialApp entries", error=str(e))

    available_providers_list = sorted(list(available_providers))

    return {
        "available_social_providers": available_providers_list,
        "has_social_providers": len(available_providers_list) > 0,
    }


def turnstile_site_key(request):
    """
    Returns the Cloudflare Turnstile site key if configured.
    Used to conditionally enable Turnstile CAPTCHA on forms.
    """
    return {"turnstile_site_key": get_turnstile_site_key()}


def referrer_banner(request):
    """
    Adds referrer banner to context. Priority order:
    1. Exact match on ref or utm_source parameter (e.g., ProductHunt)
    2. Black Friday banner as fallback (if it exists and is active)
    Only displays one banner at most.
    """
    from django.db.models import Q
    from core.models import ReferrerBanner

    referrer_code = request.GET.get("ref") or request.GET.get("utm_source")

    if referrer_code:
        try:
            banner = ReferrerBanner.objects.get(referrer=referrer_code)
            if banner.should_display:
                return {"referrer_banner": banner}
        except ReferrerBanner.DoesNotExist:
            pass

    try:
        always_on_banner = ReferrerBanner.objects.filter(
            Q(referrer_printable_name__icontains="Black Friday") | 
            Q(referrer_printable_name__icontains="Christmas Special")
        ).first()
        if always_on_banner and always_on_banner.should_display:
            return {"referrer_banner": always_on_banner}
    except ReferrerBanner.DoesNotExist:
        pass
    except ReferrerBanner.MultipleObjectsReturned:
        always_on_banner = (
            ReferrerBanner.objects.filter(referrer_printable_name__icontains="Black Friday")
            .filter(is_active=True)
            .first()
        )
        if always_on_banner and always_on_banner.should_display:
            return {"referrer_banner": always_on_banner}

    return {}
