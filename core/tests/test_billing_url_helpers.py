from django.test import override_settings

from core.views import _build_billing_home_url


@override_settings(SITE_URL="https://tuxseo.com/")
def test_build_billing_home_url_strips_trailing_site_url_slash():
    assert _build_billing_home_url() == "https://tuxseo.com/home"
