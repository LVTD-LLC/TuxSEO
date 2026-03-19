import uuid

import requests
from allauth.account.forms import LoginForm, SignupForm
from django import forms

from core.abuse_prevention import (
    get_request_ip_address,
    is_disposable_email_domain,
    is_signup_rate_limited,
)
from core.models import AutoSubmissionSetting, Profile, Project, ProjectCustomPostType
from core.turnstile import get_turnstile_secret_key, get_turnstile_site_key
from core.utils import DivErrorList
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

TURNSTILE_REASON_TOKEN_MISSING = "token_missing"
TURNSTILE_REASON_TOKEN_INVALID = "token_invalid"
TURNSTILE_REASON_TOKEN_EXPIRED = "token_expired"
TURNSTILE_REASON_PROVIDER_ERROR = "provider_error"
TURNSTILE_REASON_VALIDATION_MISMATCH = "validation_mismatch"
TURNSTILE_REASON_UNKNOWN = "unknown"


class CustomSignUpForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_class = DivErrorList

    def _get_correlation_id(self):
        if not getattr(self, "request", None):
            return str(uuid.uuid4())

        request_id_header = self.request.headers.get("X-Request-ID", "")
        request_id_meta = self.request.META.get("HTTP_X_REQUEST_ID", "")

        return request_id_header or request_id_meta or str(uuid.uuid4())

    def _get_turnstile_failure_message(self, reason_code):
        if reason_code == TURNSTILE_REASON_TOKEN_EXPIRED:
            return "Verification expired. Please complete the challenge again and retry."

        if reason_code == TURNSTILE_REASON_PROVIDER_ERROR:
            return "We couldn't validate your verification right now. Please retry in a moment."

        if reason_code == TURNSTILE_REASON_TOKEN_MISSING:
            return "Please complete the verification challenge before signing up."

        return "Verification failed. Please retry the challenge and submit again."

    def clean(self):
        cleaned_data = super().clean()

        remote_ip_address = ""
        if getattr(self, "request", None):
            remote_ip_address = get_request_ip_address(self.request)

        correlation_id = self._get_correlation_id()

        if is_signup_rate_limited(remote_ip_address):
            logger.warning(
                "[Signup Rate Limit] Signup blocked due to too many attempts",
                ip_address=remote_ip_address,
                correlation_id=correlation_id,
            )
            raise forms.ValidationError(
                "Too many signup attempts from your network. Please try again in a few minutes."
            )

        email_address = (cleaned_data.get("email") or "").strip().lower()
        if email_address and is_disposable_email_domain(email_address):
            logger.warning(
                "[Signup Disposable Email] Signup blocked for disposable email domain",
                email_domain=email_address.rsplit("@", 1)[1],
                correlation_id=correlation_id,
            )
            raise forms.ValidationError(
                "Please use a permanent email address (disposable inboxes are not allowed)."
            )

        if get_turnstile_site_key():
            turnstile_token = (self.data.get("cf-turnstile-response") or "").strip()

            if not turnstile_token:
                logger.warning(
                    "[Turnstile Validation] Missing Turnstile token in signup form",
                    reason_code=TURNSTILE_REASON_TOKEN_MISSING,
                    ip_address=remote_ip_address,
                    correlation_id=correlation_id,
                )
                raise forms.ValidationError(
                    self._get_turnstile_failure_message(TURNSTILE_REASON_TOKEN_MISSING)
                )

            verification_result = self._verify_turnstile_token(turnstile_token, remote_ip_address)

            if not verification_result["success"]:
                reason_code = verification_result["reason_code"]
                logger.warning(
                    "[Turnstile Validation] Signup blocked due to failed verification",
                    reason_code=reason_code,
                    ip_address=remote_ip_address,
                    correlation_id=correlation_id,
                    error_codes=verification_result.get("error_codes", []),
                )
                raise forms.ValidationError(self._get_turnstile_failure_message(reason_code))

            logger.info(
                "[Turnstile Validation] Signup verification succeeded",
                reason_code="verified",
                ip_address=remote_ip_address,
                correlation_id=correlation_id,
            )

        return cleaned_data

    def _map_turnstile_error_codes(self, error_codes):
        if not error_codes:
            return TURNSTILE_REASON_UNKNOWN

        error_codes_set = set(error_codes)

        if "timeout-or-duplicate" in error_codes_set:
            return TURNSTILE_REASON_TOKEN_EXPIRED

        if {
            "invalid-input-response",
            "missing-input-response",
            "invalid-widget-id",
            "invalid-parsed-domain",
        } & error_codes_set:
            return TURNSTILE_REASON_TOKEN_INVALID

        if {
            "invalid-input-secret",
            "missing-input-secret",
            "bad-request",
        } & error_codes_set:
            return TURNSTILE_REASON_VALIDATION_MISMATCH

        if {
            "internal-error",
        } & error_codes_set:
            return TURNSTILE_REASON_PROVIDER_ERROR

        return TURNSTILE_REASON_UNKNOWN

    def _verify_turnstile_token(self, token, remote_ip_address=""):
        turnstile_secret_key = get_turnstile_secret_key()

        if not turnstile_secret_key:
            logger.error(
                "[Turnstile Validation] Secret key missing while Turnstile site key is configured"
            )
            return {
                "success": False,
                "reason_code": TURNSTILE_REASON_PROVIDER_ERROR,
                "error_codes": [],
            }

        verification_payload = {
            "secret": turnstile_secret_key,
            "response": token,
        }
        if remote_ip_address:
            verification_payload["remoteip"] = remote_ip_address

        try:
            response = requests.post(
                TURNSTILE_VERIFY_URL,
                data=verification_payload,
                timeout=10,
            )

            result = response.json()
            success = result.get("success", False)
            error_codes = result.get("error-codes", [])

            if not success:
                reason_code = self._map_turnstile_error_codes(error_codes)
                logger.warning(
                    "[Turnstile Validation] Verification failed",
                    reason_code=reason_code,
                    error_codes=error_codes,
                )
                return {
                    "success": False,
                    "reason_code": reason_code,
                    "error_codes": error_codes,
                }

            return {
                "success": True,
                "reason_code": "verified",
                "error_codes": [],
            }

        except requests.RequestException as error:
            logger.error(
                "[Turnstile Validation] Request error during verification",
                error=str(error),
                exc_info=True,
            )
            return {
                "success": False,
                "reason_code": TURNSTILE_REASON_PROVIDER_ERROR,
                "error_codes": [],
            }


class CustomLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_class = DivErrorList


class ProfileUpdateForm(forms.ModelForm):
    first_name = forms.CharField(max_length=30)
    last_name = forms.CharField(max_length=30)
    email = forms.EmailField()

    class Meta:
        model = Profile
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["email"].initial = self.instance.user.email

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            profile.save()
        return profile


class ProjectScanForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["url"]


class PlausibleIntegrationForm(forms.Form):
    site_id = forms.CharField(
        max_length=255,
        label="Site ID",
        widget=forms.TextInput(
            attrs={
                "class": "block px-3 py-2 w-full text-sm text-gray-900 bg-white rounded-md border border-gray-300 focus:ring-gray-500 focus:border-gray-500",
                "placeholder": "example.com",
            }
        ),
    )
    api_key = forms.CharField(
        max_length=255,
        label="API Key",
        widget=forms.PasswordInput(
            render_value=True,
            attrs={
                "class": "block px-3 py-2 w-full text-sm text-gray-900 bg-white rounded-md border border-gray-300 focus:ring-gray-500 focus:border-gray-500",
                "placeholder": "plausible_api_key",
            },
        ),
    )
    base_url = forms.URLField(
        required=False,
        initial="https://plausible.io",
        label="Plausible URL",
        widget=forms.URLInput(
            attrs={
                "class": "block px-3 py-2 w-full text-sm text-gray-900 bg-white rounded-md border border-gray-300 focus:ring-gray-500 focus:border-gray-500",
                "placeholder": "https://plausible.io",
            }
        ),
    )

    def clean_site_id(self):
        return self.cleaned_data["site_id"].strip()

    def clean_api_key(self):
        return self.cleaned_data["api_key"].strip()

    def clean_base_url(self):
        base_url = (self.cleaned_data.get("base_url") or "https://plausible.io").strip()
        return base_url.rstrip("/")


class AutoSubmissionSettingForm(forms.ModelForm):
    TIMEZONE_CHOICES = [
        ("UTC", "UTC"),
        ("America/New_York", "America/New_York"),
        ("America/Chicago", "America/Chicago"),
        ("America/Denver", "America/Denver"),
        ("America/Los_Angeles", "America/Los_Angeles"),
        ("Europe/London", "Europe/London"),
        ("Europe/Paris", "Europe/Paris"),
        ("Asia/Tokyo", "Asia/Tokyo"),
        ("Asia/Shanghai", "Asia/Shanghai"),
        ("Asia/Kolkata", "Asia/Kolkata"),
        ("Australia/Sydney", "Australia/Sydney"),
    ]
    preferred_timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, required=False)

    class Meta:
        model = AutoSubmissionSetting
        fields = [
            "endpoint_url",
            "body",
            "header",
            "posts_per_month",
            # "preferred_timezone",
            # "preferred_time",
        ]

    def clean_body(self):
        import json

        data = self.cleaned_data["body"]
        if isinstance(data, dict):
            return data
        try:
            return json.loads(data) if data else {}
        except Exception:
            raise


class ProjectCustomPostTypeForm(forms.ModelForm):
    class Meta:
        model = ProjectCustomPostType
        fields = ["name", "prompt_guidance", "logo"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "block px-3 py-2 w-full text-sm text-gray-900 bg-white rounded-md border border-gray-300 focus:ring-gray-500 focus:border-gray-500",
                    "placeholder": "Technical",
                    "maxlength": "80",
                }
            ),
            "prompt_guidance": forms.Textarea(
                attrs={
                    "class": "block px-3 py-2 w-full text-sm text-gray-900 bg-white rounded-md border border-gray-300 focus:ring-gray-500 focus:border-gray-500",
                    "rows": 4,
                    "placeholder": "Describe style, depth, and audience for this post type.",
                    "maxlength": "1200",
                }
            ),
            "logo": forms.FileInput(
                attrs={
                    "class": "block w-full text-sm text-gray-900 rounded-md border border-gray-300 cursor-pointer bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-500 focus:border-gray-500",
                    "accept": "image/png,image/jpeg,image/webp,image/gif",
                }
            ),
        }

    def clean_name(self):
        return " ".join((self.cleaned_data.get("name") or "").split()).strip()

    def clean_prompt_guidance(self):
        return (self.cleaned_data.get("prompt_guidance") or "").strip()

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if not logo:
            return logo

        content_type = getattr(logo, "content_type", "")
        if content_type not in ProjectCustomPostType.logo_allowed_content_types:
            raise forms.ValidationError(
                "Unsupported logo format. Use PNG, JPG, WEBP, or GIF."
            )

        if logo.size > ProjectCustomPostType.logo_max_file_size_bytes:
            raise forms.ValidationError("Logo must be 2MB or smaller.")

        return logo
