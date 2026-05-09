import re
import uuid
from urllib.parse import urlencode

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_q.tasks import async_task

from core.choices import EmailType
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

User = get_user_model()


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom adapter to track email confirmations and welcome emails.
    """

    def _generate_unique_username_from_email(self, email):
        base_username = re.sub(r"[^\w]", "", (email or "").split("@")[0])
        if not base_username:
            base_username = f"user{uuid.uuid4().hex[:8]}"

        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        return username

    def populate_username(self, request, user):
        """Ensure username exists even when signup form does not request it."""
        if not user.username:
            user.username = self._generate_unique_username_from_email(user.email)

    def is_open_for_signup(self, request):
        """Allow operators to pause new registrations without affecting existing users."""
        return settings.ALLOW_SIGNUPS and super().is_open_for_signup(request)

    def send_confirmation_mail(self, request, emailconfirmation, signup):
        """
        Override to track email confirmation sends.

        Args:
            request: The HTTP request
            emailconfirmation: The email confirmation object
            signup: Boolean indicating if this is during signup (True) or resend (False)
        """
        profile = (
            emailconfirmation.email_address.user.profile
            if hasattr(emailconfirmation.email_address.user, "profile")
            else None
        )

        # Track as welcome email during signup, confirmation email on resend
        email_type = EmailType.WELCOME if signup else EmailType.EMAIL_CONFIRMATION

        logger.info(
            "[Send Confirmation Mail] Sending email",
            signup=signup,
            email_type=email_type,
            user_id=emailconfirmation.email_address.user.id,
            email=emailconfirmation.email_address.email,
        )

        try:
            result = super().send_confirmation_mail(request, emailconfirmation, signup)
            async_task(
                "core.tasks.track_email_sent",
                email_address=emailconfirmation.email_address.email,
                email_type=email_type,
                profile=profile,
                group="Track Email Sent",
            )
            return result
        except Exception as error:
            logger.error(
                "[Send Confirmation Mail] Failed to send email",
                error=str(error),
                exc_info=True,
                user_id=emailconfirmation.email_address.user.id,
                email=emailconfirmation.email_address.email,
            )
            raise

    def get_email_verification_redirect_url(self, email_address):
        profile = getattr(email_address.user, "profile", None)
        has_no_projects = bool(profile and not profile.projects.exists())

        query_parameters = {"email_confirmed": "true"}
        if has_no_projects:
            query_parameters["welcome"] = "true"

        home_url = reverse("home")
        return f"{home_url}?{urlencode(query_parameters)}"


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter to automatically generate usernames from email addresses
    during social authentication signup, bypassing the username selection page.
    """

    def is_open_for_signup(self, request, sociallogin):
        """Mirror email signup gating for social-account auto-signups."""
        return settings.ALLOW_SIGNUPS and super().is_open_for_signup(request, sociallogin)

    def populate_user(self, request, sociallogin, data):
        """
        Automatically set username from email address before user creation.
        Uses the part before @ symbol as username, ensuring uniqueness.
        """
        user = super().populate_user(request, sociallogin, data)

        if not user.username and user.email:
            base_username = re.sub(r"[^\w]", "", user.email.split("@")[0])
            if not base_username:
                base_username = f"user{uuid.uuid4().hex[:8]}"
            username = base_username

            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            user.username = username

        return user
