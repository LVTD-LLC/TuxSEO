from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse


@pytest.mark.django_db
@override_settings(SITE_URL="https://tuxseo.com")
def test_checkout_session_uses_canonical_site_url_for_success_and_cancel(client):
    user = User.objects.create_user(
        username="billing-url-user",
        email="billing-url@example.com",
        password="secret",
    )
    client.force_login(user)

    fake_price = SimpleNamespace(id="price_test")
    fake_customer = SimpleNamespace(id="cus_test")
    fake_checkout_session = SimpleNamespace(
        id="cs_test",
        url="https://checkout.stripe.test/session/cs_test",
    )

    with patch("core.views.get_price_for_product_name", return_value=fake_price):
        with patch(
            "core.views.djstripe_models.Customer.get_or_create",
            return_value=(fake_customer, True),
        ):
            with patch(
                "core.views.stripe.checkout.Session.create",
                return_value=fake_checkout_session,
            ) as mock_checkout_create:
                with patch("core.views.async_task"):
                    response = client.get(
                        reverse("user_upgrade_checkout_session", kwargs={"product_name": "Pro"})
                    )

    assert response.status_code == 303
    assert response.url == fake_checkout_session.url

    kwargs = mock_checkout_create.call_args.kwargs
    assert kwargs["success_url"] == "https://tuxseo.com/home?payment=success"
    assert kwargs["cancel_url"] == "https://tuxseo.com/home?payment=cancelled"


@pytest.mark.django_db
@override_settings(SITE_URL="https://tuxseo.com")
def test_customer_portal_session_uses_canonical_site_url_for_return(client):
    user = User.objects.create_user(
        username="portal-url-user",
        email="portal-url@example.com",
        password="secret",
    )
    client.force_login(user)

    fake_customer = SimpleNamespace(id="cus_portal_test")
    fake_portal_session = SimpleNamespace(url="https://billing.stripe.test/session/bps_test")

    with patch(
        "core.views.djstripe_models.Customer.objects.get",
        return_value=fake_customer,
    ):
        with patch(
            "core.views.stripe.billing_portal.Session.create",
            return_value=fake_portal_session,
        ) as mock_portal_create:
            response = client.get(reverse("create_customer_portal_session"))

    assert response.status_code == 302
    assert response.url == fake_portal_session.url

    kwargs = mock_portal_create.call_args.kwargs
    assert kwargs["return_url"] == "https://tuxseo.com/home"
