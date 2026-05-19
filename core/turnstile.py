import os

from django.conf import settings


def get_turnstile_site_key() -> str:
    """Return Turnstile site key with backward-compatible env fallbacks."""

    return (
        (getattr(settings, "CLOUDFLARE_TURNSTILE_SITEKEY", "") or "").strip()
        or os.getenv("CLOUDFLARE_TURNSTILE_SITE_KEY", "").strip()
        or os.getenv("TURNSTILE_SITE_KEY", "").strip()
    )


def get_turnstile_secret_key() -> str:
    """Return Turnstile secret key with backward-compatible env fallbacks."""

    return (
        (getattr(settings, "CLOUDFLARE_TURNSTILE_SECRET_KEY", "") or "").strip()
        or os.getenv("CLOUDFLARE_TURNSTILE_SECRET", "").strip()
        or os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    )
