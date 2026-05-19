from urllib.parse import parse_qs, urlparse

import pytest
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Project


@pytest.mark.django_db
def test_confirm_email_on_get_marks_email_verified_and_redirects_to_onboarding(client):
    user = User.objects.create_user(
        username="confirm-on-get-user",
        email="confirm-on-get@example.com",
        password="secret",
    )
    email_address = EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=False,
    )
    email_confirmation = EmailConfirmationHMAC.create(email_address)

    response = client.get(reverse("account_confirm_email", args=[email_confirmation.key]))

    parsed_redirect = urlparse(response.url)
    redirect_query = parse_qs(parsed_redirect.query)

    email_address.refresh_from_db()

    assert response.status_code == 302
    assert parsed_redirect.path == reverse("home")
    assert redirect_query["email_confirmed"] == ["true"]
    assert redirect_query["welcome"] == ["true"]
    assert email_address.verified is True


@pytest.mark.django_db
def test_confirm_email_link_is_idempotent_after_already_verified(client):
    user = User.objects.create_user(
        username="idempotent-confirm-user",
        email="idempotent-confirm@example.com",
        password="secret",
    )
    email_address = EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=False,
    )
    email_confirmation = EmailConfirmationHMAC.create(email_address)

    client.get(reverse("account_confirm_email", args=[email_confirmation.key]))
    response = client.get(reverse("account_confirm_email", args=[email_confirmation.key]))

    parsed_redirect = urlparse(response.url)
    redirect_query = parse_qs(parsed_redirect.query)

    assert response.status_code == 302
    assert parsed_redirect.path == reverse("home")
    assert redirect_query["email_confirmed"] == ["true"]
    assert redirect_query["welcome"] == ["true"]


@pytest.mark.django_db
def test_confirm_email_redirect_skips_onboarding_for_existing_project(client):
    user = User.objects.create_user(
        username="existing-project-confirm-user",
        email="existing-project-confirm@example.com",
        password="secret",
    )
    Project.objects.create(
        profile=user.profile,
        url="https://existing-project.example.com",
        name="Existing Project",
    )
    email_address = EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=False,
    )
    email_confirmation = EmailConfirmationHMAC.create(email_address)

    response = client.get(reverse("account_confirm_email", args=[email_confirmation.key]))

    parsed_redirect = urlparse(response.url)
    redirect_query = parse_qs(parsed_redirect.query)

    assert response.status_code == 302
    assert parsed_redirect.path == reverse("home")
    assert redirect_query["email_confirmed"] == ["true"]
    assert "welcome" not in redirect_query
