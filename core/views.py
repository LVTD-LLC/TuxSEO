import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import markdown
import requests
import stripe
from allauth.account import app_settings as account_app_settings
from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress, EmailConfirmation
from allauth.account.views import ConfirmEmailView, SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core import signing
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DeleteView, DetailView, ListView, TemplateView, UpdateView
from django_q.tasks import async_task
from djstripe import models as djstripe_models
from weasyprint import HTML

from core.analytics import ANALYTICS_EVENTS
from core.choices import BlogPostStatus, ContentType, Language, OGImageStyle, ProfileStates
from core.forms import (
    AutoSubmissionSettingForm,
    PlausibleIntegrationForm,
    ProfileUpdateForm,
    ProjectScanForm,
)
from core.models import (
    AutoSubmissionSetting,
    BlogPost,
    Competitor,
    GeneratedBlogPost,
    KeywordTrend,
    Profile,
    ProfileStateTransition,
    Project,
    ProjectIntegration,
)
from core.outcome_attribution import get_project_reporting_snapshot
from core.tasks import (
    track_event,
    try_create_posthog_alias,
)
from tuxseo.utils import get_tuxseo_logger

stripe.api_key = settings.STRIPE_SECRET_KEY


logger = get_tuxseo_logger(__name__)

User = get_user_model()


def get_google_integrations_callback_url():
    site_url = settings.SITE_URL.rstrip("/")
    return f"{site_url}{reverse('project_integrations_google_callback')}"


PRICE_SELECTION_RULES = {
    "Pro - Monthly": {
        "amount": 9900,
        "env_var": "STRIPE_PRICE_ID_PRO_MONTHLY",
    },
    "Pro - Yearly": {
        "amount": 99000,
        "env_var": "STRIPE_PRICE_ID_PRO_YEARLY",
    },
}


def _price_matches_rule(price, *, expected_amount=None):
    if expected_amount is None:
        return True
    return getattr(price, "unit_amount", None) == expected_amount


def _stripe_price_matches_rule(stripe_price, *, expected_amount=None):
    if expected_amount is None:
        return True
    return stripe_price.get("unit_amount") == expected_amount


def get_price_for_product_name(product_name):
    """Get the canonical active Stripe price for a product name.

    Selection order:
    1) Explicit env override (STRIPE_PRICE_ID_PRO_MONTHLY / STRIPE_PRICE_ID_PRO_YEARLY)
    2) dj-stripe active price matching canonical amount
    3) Stripe API active price matching canonical amount (synced into dj-stripe)
    4) Backward-compatible fallback to any active product price
    """
    rule = PRICE_SELECTION_RULES.get(product_name, {})
    expected_amount = rule.get("amount")
    env_var = rule.get("env_var")
    explicit_price_id = getattr(settings, env_var, "") if env_var else ""

    if explicit_price_id:
        try:
            return djstripe_models.Price.objects.select_related("product").get(
                id=explicit_price_id,
                livemode=settings.STRIPE_LIVE_MODE,
                active=True,
            )
        except djstripe_models.Price.DoesNotExist:
            stripe_price = stripe.Price.retrieve(explicit_price_id, expand=["product"])
            synced_price = djstripe_models.Price.sync_from_stripe_data(stripe_price)
            if (
                synced_price.product.name == product_name
                and synced_price.active
                and _price_matches_rule(synced_price, expected_amount=expected_amount)
            ):
                return synced_price
        except djstripe_models.Price.MultipleObjectsReturned:
            pass

    try:
        return djstripe_models.Price.objects.select_related("product").get(
            product__name=product_name,
            livemode=settings.STRIPE_LIVE_MODE,
            active=True,
            unit_amount=expected_amount,
        )
    except (djstripe_models.Price.DoesNotExist, djstripe_models.Price.MultipleObjectsReturned):
        pass

    stripe_prices = stripe.Price.list(active=True, expand=["data.product"], limit=100)
    fallback_match = None

    for stripe_price in stripe_prices.auto_paging_iter():
        stripe_product = stripe_price.get("product")
        stripe_product_name = getattr(stripe_product, "name", None)

        if stripe_product_name != product_name:
            continue

        if _stripe_price_matches_rule(stripe_price, expected_amount=expected_amount):
            synced_price = djstripe_models.Price.sync_from_stripe_data(stripe_price)
            return synced_price

        if fallback_match is None:
            fallback_match = stripe_price

    if fallback_match is not None:
        synced_price = djstripe_models.Price.sync_from_stripe_data(fallback_match)
        return synced_price

    raise djstripe_models.Price.DoesNotExist


class LandingView(TemplateView):
    template_name = "pages/landing.html"


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        welcome_status = self.request.GET.get("welcome")
        if welcome_status == "true":
            context["show_onboarding_modal"] = True
            context["show_confetti"] = True

        payment_status = self.request.GET.get("payment")
        if payment_status == "success":
            messages.success(self.request, "Thanks for subscribing, I hope you enjoy the app!")
            context["show_confetti"] = True
        elif payment_status == "failed":
            messages.error(self.request, "Something went wrong with the payment.")

        context["form"] = ProjectScanForm()

        user = self.request.user
        profile = user.profile

        projects = (
            Project.objects.filter(profile=profile)
            .annotate(
                posted_posts_count=Count(
                    "generated_blog_posts", filter=Q(generated_blog_posts__posted=True)
                )
            )
            .order_by("-created_at")
        )

        projects_with_stats = []
        for project in projects:
            project_stats = {
                "project": project,
                "posted_posts_count": project.posted_posts_count,
            }
            projects_with_stats.append(project_stats)

        context["projects"] = projects_with_stats

        email_address = EmailAddress.objects.get_for_user(user, user.email)
        context["email_verified"] = email_address.verified

        return context


class AdminPanelView(LoginRequiredMixin, TemplateView):
    template_name = "pages/admin_panel.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "You don't have permission to access the admin panel.")
            return redirect(reverse("home"))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        total_users = User.objects.count()
        verified_users = (
            EmailAddress.objects.filter(verified=True).values("user").distinct().count()
        )

        total_projects = Project.objects.count()
        total_blog_posts = GeneratedBlogPost.objects.count()
        posted_blog_posts = GeneratedBlogPost.objects.filter(posted=True).count()

        paid_subscribers = (
            Profile.objects.filter(product__isnull=False).exclude(product__name="Free").count()
        )

        recent_users_data = User.objects.select_related("profile").order_by("-date_joined")[:5]
        recent_users = []
        for user in recent_users_data:
            email_address = EmailAddress.objects.filter(user=user, email=user.email).first()
            recent_users.append(
                {
                    "username": user.username,
                    "email": user.email,
                    "date_joined": user.date_joined,
                    "is_verified": email_address.verified if email_address else False,
                }
            )

        recent_projects = Project.objects.select_related("profile__user").order_by("-created_at")[
            :5
        ]

        context["stats"] = {
            "total_users": total_users,
            "verified_users": verified_users,
            "total_projects": total_projects,
            "total_blog_posts": total_blog_posts,
            "posted_blog_posts": posted_blog_posts,
            "paid_subscribers": paid_subscribers,
            "recent_users": recent_users,
            "recent_projects": recent_projects,
        }

        return context


class AccountSignupView(SignupView):
    template_name = "account/signup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["signup_started_event_name"] = ANALYTICS_EVENTS.SIGNUP_STARTED
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        user = self.user
        profile = user.profile

        if settings.POSTHOG_API_KEY:
            async_task(
                try_create_posthog_alias,
                profile_id=profile.id,
                cookies=self.request.COOKIES,
                source_function="AccountSignupView - form_valid",
                group="Create Posthog Alias",
            )

        async_task(
            track_event,
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.SIGNUP_COMPLETED,
            properties={
                "$set": {
                    "email": profile.user.email,
                    "username": profile.user.username,
                },
            },
            source_function="AccountSignupView - form_valid",
            group="Track Event",
        )

        return response

    def get_success_url(self):
        success_url = super().get_success_url() or reverse("home")
        welcome_params = {"welcome": "true"}
        return f"{success_url}?{urlencode(welcome_params)}"


class OnboardingFriendlyConfirmEmailView(ConfirmEmailView):
    def get(self, *args, **kwargs):
        confirmation_key = self.kwargs.get("key", "")
        verified_email_address = self.get_verified_email_address_for_used_hmac_key(
            confirmation_key=confirmation_key
        )
        if verified_email_address:
            messages.info(self.request, "Your email is already verified.")
            redirect_url = get_adapter(self.request).get_email_verification_redirect_url(
                verified_email_address
            )
            return redirect(redirect_url)

        return super().get(*args, **kwargs)

    def get_verified_email_address_for_used_hmac_key(self, confirmation_key: str):
        if not account_app_settings.EMAIL_CONFIRMATION_HMAC:
            return None

        max_age_seconds = 60 * 60 * 24 * account_app_settings.EMAIL_CONFIRMATION_EXPIRE_DAYS
        try:
            email_address_id = signing.loads(
                confirmation_key,
                max_age=max_age_seconds,
                salt=account_app_settings.SALT,
            )
        except (signing.BadSignature, signing.SignatureExpired):
            return None

        return EmailAddress.objects.filter(id=email_address_id, verified=True).first()


class UserSettingsView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    login_url = "account_login"
    model = Profile
    form_class = ProfileUpdateForm
    success_message = "User Profile Updated"
    success_url = reverse_lazy("settings")
    template_name = "pages/user-settings.html"

    def get_object(self):
        return self.request.user.profile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        email_address = EmailAddress.objects.get_for_user(user, user.email)
        context["email_verified"] = email_address.verified
        context["resend_confirmation_url"] = reverse("resend_confirmation")
        context["has_subscription"] = user.profile.has_product_or_subscription
        context["has_pro_subscription"] = user.profile.is_on_pro_plan

        return context


@login_required
def resend_confirmation_email(request):
    user = request.user

    try:
        email_address = EmailAddress.objects.get_for_user(user, user.email)

        if not email_address:
            messages.error(request, "No email address found for your account.")
            logger.warning(
                "[Resend Confirmation] No email address found",
                user_id=user.id,
                user_email=user.email,
            )
            return redirect("settings")

        if email_address.verified:
            messages.info(request, "Your email is already verified.")
            logger.info(
                "[Resend Confirmation] Email already verified",
                user_id=user.id,
                user_email=user.email,
            )
            return redirect("settings")

        # Create or get existing email confirmation
        email_confirmation = EmailConfirmation.create(email_address)
        email_confirmation.send(request, signup=False)

        messages.success(request, "Confirmation email has been sent. Please check your inbox.")
        logger.info(
            "[Resend Confirmation] Email sent successfully",
            user_id=user.id,
            user_email=user.email,
        )

    except Exception as e:
        messages.error(request, "Failed to send confirmation email. Please try again later.")
        logger.error(
            "[Resend Confirmation] Failed to send email",
            user_id=user.id,
            user_email=user.email,
            error=str(e),
            exc_info=True,
        )

    return redirect("settings")


class PricingView(TemplateView):
    template_name = "pages/pricing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        number_of_subscribed_users = Profile.objects.filter(state=ProfileStates.SUBSCRIBED).count()

        if self.request.user.is_authenticated:
            try:
                profile = self.request.user.profile
                context["has_pro_subscription"] = profile.has_product_or_subscription
                context["current_product_name"] = profile.product_name
            except Profile.DoesNotExist:
                context["has_pro_subscription"] = False
                context["current_product_name"] = "Free"
        else:
            context["has_pro_subscription"] = False
            context["current_product_name"] = "Free"

        context["early_bird_spots_left"] = 20 - number_of_subscribed_users - 1

        return context


class PrivacyPolicyView(TemplateView):
    template_name = "pages/privacy_policy.html"


class TermsOfServiceView(TemplateView):
    template_name = "pages/terms_of_service.html"


def _build_billing_home_url() -> str:
    """Return the canonical app home URL for Stripe return links."""
    return f"{settings.SITE_URL.rstrip('/')}{reverse('home')}"


@login_required
def create_checkout_session(request, product_name):
    """Create a new subscription checkout session for users without active subscriptions."""
    user = request.user
    profile = user.profile

    # Superusers already have subscription access
    if user.is_superuser:
        logger.warning(
            "[CreateCheckout] Superuser attempted to create checkout session",
            user_id=user.id,
        )
        messages.info(request, "Superusers already have full access.")
        return redirect(reverse("home"))

    try:
        price = get_price_for_product_name(product_name)
    except djstripe_models.Price.DoesNotExist:
        logger.error(
            "[CreateCheckout] Price not found",
            user_id=user.id,
            product_name=product_name,
        )
        messages.error(request, "Product not found. Please contact support.")
        return redirect(reverse("home"))

    # Get or create customer
    customer, _ = djstripe_models.Customer.get_or_create(subscriber=user)

    if not profile.customer and isinstance(customer, djstripe_models.Customer):
        profile.customer = customer
        profile.save(update_fields=["customer"])

    # Check if user already has an active subscription
    if profile.subscription:
        logger.warning(
            "[CreateCheckout] User already has active subscription",
            user_id=user.id,
            subscription_id=profile.subscription.id,
        )
        messages.info(
            request,
            "You already have an active subscription. Use the upgrade option to change plans.",
        )
        return redirect(reverse("home"))

    # Create checkout session
    logger.info(
        "[CreateCheckout] Creating new checkout session",
        user_id=user.id,
        profile_id=profile.id,
        product_name=product_name,
    )

    async_task(
        track_event,
        profile_id=profile.id,
        event_name=ANALYTICS_EVENTS.CHECKOUT_STARTED,
        properties={
            "product_name": product_name,
            "price_id": price.id,
            "source": "create_checkout_session",
        },
        source_function="create_checkout_session",
        group="Track Event",
    )

    billing_home_url = _build_billing_home_url()

    success_url = f"{billing_home_url}?{urlencode({'payment': 'success'})}"
    cancel_url = f"{billing_home_url}?{urlencode({'payment': 'cancelled'})}"

    try:
        checkout_session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=["card"],
            allow_promotion_codes=True,
            automatic_tax={"enabled": True},
            line_items=[
                {
                    "price": price.id,
                    "quantity": 1,
                }
            ],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_update={
                "address": "auto",
            },
            subscription_data={
                "metadata": {
                    "user_id": user.id,
                    "profile_id": profile.id,
                    "product_name": product_name,
                }
            },
            metadata={
                "user_id": user.id,
                "profile_id": profile.id,
                "price_id": price.id,
                "action": "new_subscription",
            },
            client_reference_id=f"user_{user.id}_profile_{profile.id}_{int(time.time())}",
        )

        logger.info(
            "[CreateCheckout] Created checkout session",
            user_id=user.id,
            profile_id=profile.id,
            product_name=product_name,
            checkout_session_id=checkout_session.id,
        )

        response = HttpResponseRedirect(checkout_session.url)
        response.status_code = 303
        return response

    except stripe.error.StripeError as e:
        logger.error(
            "[CreateCheckout] Stripe error creating checkout session",
            user_id=user.id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        messages.error(request, "Unable to create checkout session. Please try again.")
        return redirect(reverse("home"))


@login_required
def upgrade_subscription(request, product_name):
    """Upgrade or downgrade an existing active subscription."""
    user = request.user
    profile = user.profile

    # Superusers already have full access
    if user.is_superuser:
        logger.warning(
            "[UpgradeSubscription] Superuser attempted to upgrade subscription",
            user_id=user.id,
        )
        messages.info(request, "Superusers already have full access.")
        return redirect(reverse("home"))

    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect(reverse("home"))

    try:
        new_price = get_price_for_product_name(product_name)
    except djstripe_models.Price.DoesNotExist:
        logger.error(
            "[UpgradeSubscription] Price not found",
            user_id=user.id,
            product_name=product_name,
        )
        messages.error(request, "Product not found. Please contact support.")
        return redirect(reverse("home"))

    active_subscription = profile.subscription

    if not active_subscription:
        logger.warning(
            "[UpgradeSubscription] No active subscription found",
            user_id=user.id,
            profile_id=profile.id,
        )
        messages.error(request, "No active subscription found. Please create a new subscription.")
        return redirect(reverse("home"))

    logger.info(
        "[UpgradeSubscription] Processing subscription change",
        user_id=user.id,
        profile_id=profile.id,
        subscription_id=active_subscription.id,
        new_product_name=product_name,
    )

    try:
        # Get current subscription item
        subscription_item = active_subscription.items.first()

        if not subscription_item:
            logger.error(
                "[UpgradeSubscription] No subscription items found",
                user_id=user.id,
                subscription_id=active_subscription.id,
            )
            messages.error(request, "Unable to modify subscription. Please contact support.")
            return redirect(reverse("home"))

        current_price = subscription_item.price
        current_product = current_price.product

        # Check if already on this plan
        if current_price.id == new_price.id:
            messages.info(request, f"You are already subscribed to {product_name}.")
            return redirect(reverse("home"))

        # Determine if upgrade or downgrade based on unit amount
        current_amount = current_price.unit_amount or 0
        new_amount = new_price.unit_amount or 0
        is_upgrade = new_amount > current_amount
        action_type = "upgrade" if is_upgrade else "downgrade"

        # Update the subscription
        updated_subscription = stripe.Subscription.modify(
            active_subscription.id,
            items=[
                {
                    "id": subscription_item.id,
                    "price": new_price.id,
                }
            ],
            proration_behavior="create_prorations",
            metadata={
                "user_id": user.id,
                "profile_id": profile.id,
                "action": action_type,
                "from_product": current_product.name,
                "to_product": product_name,
            },
        )

        # Sync with dj-stripe
        synced_subscription = djstripe_models.Subscription.sync_from_stripe_data(
            updated_subscription
        )

        # Update profile
        profile.subscription = synced_subscription
        profile.product = new_price.product
        profile.save(update_fields=["subscription", "product", "updated_at"])

        # Log state transition
        ProfileStateTransition.objects.create(
            profile=profile,
            from_state=profile.state if hasattr(profile, "state") else "active",
            to_state="active",
            backup_profile_id=profile.id,
            metadata={
                "action": f"subscription_{action_type}",
                "from_product": current_product.name,
                "from_price_id": current_price.id,
                "to_product": product_name,
                "to_price_id": new_price.id,
                "subscription_id": synced_subscription.id,
            },
        )

        action_word = "upgraded" if is_upgrade else "changed"
        messages.success(request, f"Successfully {action_word} to {product_name}!")

        logger.info(
            "[UpgradeSubscription] Successfully modified subscription",
            user_id=user.id,
            profile_id=profile.id,
            subscription_id=synced_subscription.id,
            action=action_type,
            from_product=current_product.name,
            to_product=product_name,
        )

        return redirect(reverse("home") + "?payment=success")

    except stripe.error.StripeError as e:
        logger.error(
            "[UpgradeSubscription] Stripe error",
            user_id=user.id,
            subscription_id=active_subscription.id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        messages.error(
            request, "Unable to modify subscription. Please try again or contact support."
        )
        return redirect(reverse("home"))


@login_required
def create_customer_portal_session(request):
    user = request.user
    customer = djstripe_models.Customer.objects.get(subscriber=user)

    session = stripe.billing_portal.Session.create(
        customer=customer.id,
        return_url=_build_billing_home_url(),
    )

    return redirect(session.url, code=303)


class BlogView(ListView):
    model = BlogPost
    template_name = "blog/blog_posts.html"
    context_object_name = "blog_posts"

    def get_queryset(self):
        return BlogPost.objects.filter(status=BlogPostStatus.PUBLISHED).order_by("-created_at")


class BlogPostView(DetailView):
    model = BlogPost
    template_name = "blog/blog_post.html"
    context_object_name = "blog_post"

    def get_queryset(self):
        return BlogPost.objects.filter(status=BlogPostStatus.PUBLISHED).order_by(
            "-created_at", "-id"
        )

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        slug = self.kwargs.get(self.slug_url_kwarg)

        blog_post = queryset.filter(**{self.slug_field: slug}).first()
        if blog_post is None:
            raise Http404("No blog post found matching the query")

        return blog_post


class ProjectHomeView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_home.html"
    context_object_name = "project"

    loading_window_minutes = 20

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def get_content_state(self, item_count: int, is_project_recently_created: bool) -> dict:
        is_loading_state = item_count == 0 and is_project_recently_created
        is_empty_state = item_count == 0 and not is_project_recently_created
        return {
            "count": item_count,
            "is_loading_state": is_loading_state,
            "is_empty_state": is_empty_state,
            "has_items": item_count > 0,
        }

    def get_reporting_snapshot_state(self, *, project: Project) -> dict:
        window_end = timezone.now().date()
        window_start = window_end - timedelta(days=29)
        snapshot = get_project_reporting_snapshot(
            project=project,
            start_date=window_start,
            end_date=window_end,
        )

        trend = snapshot["trend"]
        latest = trend[-1] if trend else {
            "seo_outcome_value": 0.0,
            "ai_visibility_signal_value": 0.0,
            "event_count": 0,
        }

        sparkline = []
        max_value = max((item["seo_outcome_value"] for item in trend), default=0.0)
        for item in trend[-14:]:
            relative_height = int((item["seo_outcome_value"] / max_value) * 100) if max_value > 0 else 0
            sparkline.append(
                {
                    "date": item["date"],
                    "value": item["seo_outcome_value"],
                    "height_pct": max(relative_height, 8) if item["seo_outcome_value"] > 0 else 6,
                }
            )

        return {
            "snapshot": snapshot,
            "latest": latest,
            "sparkline": sparkline,
            "is_empty": snapshot["event_count"] == 0,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object

        is_project_recently_created = (
            project.created_at
            >= timezone.now() - timedelta(minutes=self.loading_window_minutes)
        )

        title_ideas_state = self.get_content_state(
            item_count=project.blog_post_title_suggestions.count(),
            is_project_recently_created=is_project_recently_created,
        )
        keywords_state = self.get_content_state(
            item_count=project.project_keywords.count(),
            is_project_recently_created=is_project_recently_created,
        )
        pages_state = self.get_content_state(
            item_count=project.project_pages.count(),
            is_project_recently_created=is_project_recently_created,
        )
        competitors_state = self.get_content_state(
            item_count=project.competitors.count(),
            is_project_recently_created=is_project_recently_created,
        )

        has_project_summary = bool(project.summary.strip())
        is_summary_loading_state = not has_project_summary and is_project_recently_created
        is_summary_empty_state = not has_project_summary and not is_project_recently_created

        has_loading_generation_state = any(
            content_state["is_loading_state"]
            for content_state in [title_ideas_state, keywords_state, pages_state, competitors_state]
        )
        has_empty_generation_state = all(
            content_state["is_empty_state"]
            for content_state in [title_ideas_state, keywords_state, pages_state, competitors_state]
        )

        context["title_ideas_state"] = title_ideas_state
        context["keywords_state"] = keywords_state
        context["pages_state"] = pages_state
        context["competitors_state"] = competitors_state
        context["generated_posts_count"] = project.generated_blog_posts.count()
        context["posted_posts_count"] = project.generated_blog_posts.filter(posted=True).count()
        context["is_summary_loading_state"] = is_summary_loading_state
        context["is_summary_empty_state"] = is_summary_empty_state
        context["has_loading_generation_state"] = has_loading_generation_state
        context["has_empty_generation_state"] = has_empty_generation_state
        context["reporting_snapshot_state"] = self.get_reporting_snapshot_state(project=project)

        return context


class ProjectEyeCatchingPostsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_eye_catching_posts.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        profile = self.request.user.profile

        all_suggestions = project.blog_post_title_suggestions.filter(
            content_type=ContentType.SHARING
        ).prefetch_related("generated_blog_posts")

        project_keywords = project.get_keywords()

        archived_suggestions = []
        active_suggestions = []

        for suggestion in all_suggestions:
            suggestion.keywords_with_usage = []
            if suggestion.target_keywords:
                for keyword_text in suggestion.target_keywords:
                    keyword_info = project_keywords.get(
                        keyword_text.lower(),
                        {"keyword": None, "in_use": False, "project_keyword_id": None},
                    )
                    suggestion.keywords_with_usage.append(
                        {
                            "text": keyword_text,
                            "keyword": keyword_info["keyword"],
                            "in_use": keyword_info["in_use"],
                            "project_keyword_id": keyword_info["project_keyword_id"],
                        }
                    )

            has_posted_blog_post = any(
                blog_post.posted for blog_post in suggestion.generated_blog_posts.all()
            )
            if has_posted_blog_post:
                continue

            if suggestion.archived:
                archived_suggestions.append(suggestion)
            else:
                active_suggestions.append(suggestion)

        context["archived_suggestions"] = archived_suggestions
        context["active_suggestions"] = active_suggestions
        context["has_pro_subscription"] = profile.is_on_pro_plan
        context["has_auto_submission_setting"] = AutoSubmissionSetting.objects.filter(
            project=project
        ).exists()
        context["content_type"] = "SHARING"
        context["content_type_display"] = "Eye Catching"

        return context


class ProjectSEOPostsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_seo_posts.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        profile = self.request.user.profile

        all_suggestions = project.blog_post_title_suggestions.filter(
            content_type=ContentType.SEO
        ).prefetch_related("generated_blog_posts")

        project_keywords = project.get_keywords()

        archived_suggestions = []
        active_suggestions = []

        for suggestion in all_suggestions:
            suggestion.keywords_with_usage = []
            if suggestion.target_keywords:
                for keyword_text in suggestion.target_keywords:
                    keyword_info = project_keywords.get(
                        keyword_text.lower(),
                        {"keyword": None, "in_use": False, "project_keyword_id": None},
                    )
                    suggestion.keywords_with_usage.append(
                        {
                            "text": keyword_text,
                            "keyword": keyword_info["keyword"],
                            "in_use": keyword_info["in_use"],
                            "project_keyword_id": keyword_info["project_keyword_id"],
                        }
                    )

            has_posted_blog_post = any(
                blog_post.posted for blog_post in suggestion.generated_blog_posts.all()
            )
            if has_posted_blog_post:
                continue

            if suggestion.archived:
                archived_suggestions.append(suggestion)
            else:
                active_suggestions.append(suggestion)

        context["archived_suggestions"] = archived_suggestions
        context["active_suggestions"] = active_suggestions
        context["has_pro_subscription"] = profile.is_on_pro_plan
        context["has_auto_submission_setting"] = AutoSubmissionSetting.objects.filter(
            project=project
        ).exists()
        context["content_type"] = "SEO"
        context["content_type_display"] = "SEO Optimized"

        return context


class ProjectIntegrationsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_integrations.html"
    context_object_name = "project"

    google_scope_map = {
        ProjectIntegration.Provider.GOOGLE_ANALYTICS: [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/analytics.readonly",
        ],
        ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE: [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/webmasters.readonly",
        ],
    }

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object

        integrations = {
            integration.provider: integration for integration in project.integrations.all()
        }

        context["ga4_integration"] = integrations.get(
            ProjectIntegration.Provider.GOOGLE_ANALYTICS
        )
        context["gsc_integration"] = integrations.get(
            ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE
        )
        context["plausible_integration"] = integrations.get(ProjectIntegration.Provider.PLAUSIBLE)

        if "plausible_form" not in context:
            plausible_integration = context["plausible_integration"]
            initial = {}
            if plausible_integration:
                initial = {
                    "site_id": plausible_integration.plausible_site_id,
                    "api_key": plausible_integration.plausible_api_key,
                    "base_url": plausible_integration.plausible_base_url,
                }
            context["plausible_form"] = PlausibleIntegrationForm(initial=initial)

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action")

        if action == "connect_google_analytics":
            return self._start_google_oauth(ProjectIntegration.Provider.GOOGLE_ANALYTICS)

        if action == "connect_google_search_console":
            return self._start_google_oauth(ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE)

        if action == "disconnect_google_analytics":
            self._disconnect_google_integration(ProjectIntegration.Provider.GOOGLE_ANALYTICS)
            messages.success(request, "Google Analytics disconnected.")
            return redirect("project_integrations", pk=self.object.pk)

        if action == "disconnect_google_search_console":
            self._disconnect_google_integration(ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE)
            messages.success(request, "Google Search Console disconnected.")
            return redirect("project_integrations", pk=self.object.pk)

        if action == "connect_plausible":
            form = PlausibleIntegrationForm(request.POST)
            if form.is_valid():
                if self._connect_plausible(form.cleaned_data):
                    messages.success(request, "Plausible connected.")
                    return redirect("project_integrations", pk=self.object.pk)

                messages.error(
                    request,
                    "Could not verify Plausible credentials. Check API key/site ID and retry.",
                )
            context = self.get_context_data()
            context["plausible_form"] = form
            return self.render_to_response(context)

        if action == "disconnect_plausible":
            self._disconnect_plausible()
            messages.success(request, "Plausible disconnected.")
            return redirect("project_integrations", pk=self.object.pk)

        messages.error(request, "Unknown integration action.")
        return redirect("project_integrations", pk=self.object.pk)

    def _start_google_oauth(self, provider):
        client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
        client_secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")

        if not client_id or not client_secret:
            messages.error(
                self.request,
                "Google OAuth is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            )
            return redirect("project_integrations", pk=self.object.pk)

        nonce = secrets.token_urlsafe(24)
        session_key = f"google_integration_oauth_state:{nonce}"
        self.request.session[session_key] = {
            "project_id": self.object.pk,
            "provider": provider,
        }
        self.request.session.set_expiry(600)

        callback_url = get_google_integrations_callback_url()
        scope = self.google_scope_map[provider]

        params = {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": " ".join(scope),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": nonce,
        }

        auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return HttpResponseRedirect(auth_url)

    def _disconnect_google_integration(self, provider):
        integration, _ = ProjectIntegration.objects.get_or_create(
            project=self.object,
            provider=provider,
        )
        integration.status = ProjectIntegration.Status.DISCONNECTED
        integration.access_token = ""
        integration.refresh_token = ""
        integration.scope = ""
        integration.token_expires_at = None
        integration.connected_at = None
        integration.external_account_email = ""
        integration.save()

    def _connect_plausible(self, cleaned_data):
        base_url = cleaned_data["base_url"]
        site_id = cleaned_data["site_id"]
        api_key = cleaned_data["api_key"]

        verify_url = f"{base_url}/api/v1/stats/aggregate"
        try:
            response = requests.get(
                verify_url,
                params={
                    "site_id": site_id,
                    "period": "7d",
                    "metrics": "visitors",
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
        except requests.RequestException:
            return False

        if response.status_code != 200:
            return False

        integration, _ = ProjectIntegration.objects.get_or_create(
            project=self.object,
            provider=ProjectIntegration.Provider.PLAUSIBLE,
        )
        integration.status = ProjectIntegration.Status.CONNECTED
        integration.connected_at = timezone.now()
        integration.plausible_api_key = api_key
        integration.plausible_site_id = site_id
        integration.plausible_base_url = base_url
        integration.save()
        return True

    def _disconnect_plausible(self):
        integration, _ = ProjectIntegration.objects.get_or_create(
            project=self.object,
            provider=ProjectIntegration.Provider.PLAUSIBLE,
        )
        integration.status = ProjectIntegration.Status.DISCONNECTED
        integration.connected_at = None
        integration.plausible_api_key = ""
        integration.plausible_site_id = ""
        integration.plausible_base_url = "https://plausible.io"
        integration.save()


class ProjectIntegrationsGoogleCallbackView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        state = request.GET.get("state", "")
        oauth_error = request.GET.get("error")
        session_key = f"google_integration_oauth_state:{state}"

        oauth_state = request.session.pop(session_key, None)
        if not state or not oauth_state:
            messages.error(request, "Google integration session expired. Please retry.")
            return redirect("home")

        project = Project.objects.filter(
            pk=oauth_state["project_id"],
            profile=request.user.profile,
        ).first()
        if not project:
            raise Http404("Project not found")

        if oauth_error:
            messages.error(request, f"Google connection cancelled: {oauth_error}.")
            return redirect("project_integrations", pk=project.pk)

        code = request.GET.get("code")
        if not code:
            messages.error(request, "Google did not return an authorization code.")
            return redirect("project_integrations", pk=project.pk)

        token_response = self._exchange_google_code_for_token(
            request=request,
            code=code,
        )
        if not token_response:
            messages.error(request, "Could not complete Google OAuth token exchange.")
            return redirect("project_integrations", pk=project.pk)

        provider = oauth_state["provider"]
        self._save_google_integration(project=project, provider=provider, token_response=token_response)

        provider_label = dict(ProjectIntegration.Provider.choices).get(provider, "Google integration")
        messages.success(request, f"{provider_label} connected.")
        return redirect("project_integrations", pk=project.pk)

    def _exchange_google_code_for_token(self, request, code):
        callback_url = get_google_integrations_callback_url()
        try:
            response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": callback_url,
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            response.raise_for_status()
            token_response = response.json()
            if not token_response.get("access_token"):
                return None
            return token_response
        except (requests.RequestException, ValueError):
            logger.exception("[ProjectIntegrationsGoogleCallbackView] Google token exchange failed")
            return None

    def _fetch_google_email(self, access_token):
        try:
            response = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("email", "")
        except (requests.RequestException, ValueError):
            return ""

    def _save_google_integration(self, *, project, provider, token_response):
        integration, _ = ProjectIntegration.objects.get_or_create(
            project=project,
            provider=provider,
        )

        access_token = token_response.get("access_token", "")
        refresh_token = token_response.get("refresh_token", "")
        expires_in = token_response.get("expires_in")
        scope = token_response.get("scope", "")

        integration.status = ProjectIntegration.Status.CONNECTED
        integration.access_token = access_token
        if refresh_token:
            integration.refresh_token = refresh_token
        integration.scope = scope
        integration.connected_at = timezone.now()
        integration.external_account_email = self._fetch_google_email(access_token)

        if expires_in:
            integration.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))

        integration.save()


class ProjectSettingsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_settings.html"
    context_object_name = "project"

    def get_queryset(self):
        # Ensure users can only see their own projects
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        # Try to get existing settings for this project
        settings = AutoSubmissionSetting.objects.filter(project=project).first()
        if settings:
            form = AutoSubmissionSettingForm(instance=settings)
        else:
            form = AutoSubmissionSettingForm()
        context["auto_submission_settings_form"] = form
        context["languages"] = Language.choices
        context["og_image_styles"] = OGImageStyle.choices
        context["has_pro_subscription"] = self.request.user.profile.is_on_pro_plan

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        project = self.object
        settings = AutoSubmissionSetting.objects.filter(project=project).first()
        if settings:
            form = AutoSubmissionSettingForm(request.POST, instance=settings)
        else:
            form = AutoSubmissionSettingForm(request.POST)
        if form.is_valid():
            auto_settings = form.save(commit=False)
            auto_settings.project = project
            auto_settings.save()
            messages.success(request, "Automatic submission settings saved.")
            return redirect("project_settings", pk=project.pk)
        else:
            context = self.get_context_data()
            context["auto_submission_settings_form"] = form
            return self.render_to_response(context)


class ProjectKeywordsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_keywords.html"
    context_object_name = "project"

    def get_queryset(self):
        # Ensure users can only see their own projects
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object

        # Get all keywords associated with this project with their metrics
        project_keywords = (
            project.project_keywords.select_related("keyword")
            .prefetch_related(
                Prefetch("keyword__trends", queryset=KeywordTrend.objects.order_by("year", "month"))
            )
            .order_by("-keyword__volume", "keyword__keyword_text")
        )

        # Prepare keywords with trend data for the template and calculate counts
        keywords_with_trends = []
        total_keywords_count = 0
        used_keywords_count = 0

        for project_keyword in project_keywords:
            keyword = project_keyword.keyword

            # Get trend data for this keyword
            trend_data = [
                {"month": trend.month, "year": trend.year, "value": trend.value}
                for trend in keyword.trends.all()
            ]

            # Create keyword object with all necessary data
            keyword_data = {
                "id": keyword.id,
                "keyword_text": keyword.keyword_text,
                "volume": keyword.volume,
                "cpc_value": keyword.cpc_value,
                "cpc_currency": keyword.cpc_currency,
                "competition": keyword.competition,
                "created_at": project_keyword.created_at,
                "use": project_keyword.use,
                "trend_data": trend_data,
                "project_keyword_id": project_keyword.id,
            }
            keywords_with_trends.append(keyword_data)

            # Count keywords while we're iterating
            total_keywords_count += 1
            if project_keyword.use:
                used_keywords_count += 1

        context["keywords"] = keywords_with_trends
        context["total_keywords_count"] = total_keywords_count
        context["used_keywords_count"] = used_keywords_count
        context["available_keywords_count"] = total_keywords_count - used_keywords_count

        return context


class ProjectPagesView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_pages.html"
    context_object_name = "project"

    def get_queryset(self):
        # Ensure users can only see their own projects
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        from urllib.parse import urlparse
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        project = self.object

        # Parse the project base URL to get the domain
        project_parsed_url = urlparse(project.url)
        project_base_url = f"{project_parsed_url.scheme}://{project_parsed_url.netloc}"

        # Get all pages for this project, ordered by analyzed status and creation date
        all_project_pages = project.project_pages.order_by("-date_analyzed", "-created_at")

        # Filter to only show internal pages (same domain as project)
        filtered_pages = []
        for page in all_project_pages:
            page_parsed_url = urlparse(page.url)
            page_base_url = f"{page_parsed_url.scheme}://{page_parsed_url.netloc}"

            # Only include pages that match the project's base URL
            if page_base_url == project_base_url:
                # Add the path for display purposes
                page.url_path = page_parsed_url.path or "/"
                filtered_pages.append(page)

        # Calculate statistics (based on all filtered pages)
        total_pages_count = len(filtered_pages)
        analyzed_pages_count = sum(1 for page in filtered_pages if page.date_analyzed)
        ai_pages_count = sum(1 for page in filtered_pages if page.source == "AI")
        sitemap_pages_count = sum(1 for page in filtered_pages if page.source == "SITEMAP")

        # Paginate the filtered pages
        paginator = Paginator(filtered_pages, 50)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["pages"] = page_obj.object_list
        context["project_base_url"] = project_base_url
        context["total_pages_count"] = total_pages_count
        context["analyzed_pages_count"] = analyzed_pages_count
        context["ai_pages_count"] = ai_pages_count
        context["sitemap_pages_count"] = sitemap_pages_count
        context["unanalyzed_pages_count"] = total_pages_count - analyzed_pages_count

        return context


class ProjectCompetitorsView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "project/project_competitors.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        profile = self.request.user.profile

        competitors = project.competitors.order_by("-date_analyzed", "-created_at")

        total_competitors_count = competitors.count()
        analyzed_competitors_count = competitors.filter(date_analyzed__isnull=False).count()
        scraped_competitors_count = competitors.filter(date_scraped__isnull=False).count()

        context["competitors"] = competitors
        context["total_competitors_count"] = total_competitors_count
        context["analyzed_competitors_count"] = analyzed_competitors_count
        context["scraped_competitors_count"] = scraped_competitors_count
        context["unanalyzed_competitors_count"] = (
            total_competitors_count - analyzed_competitors_count
        )

        # Add limit information for free users
        context["competitor_limit"] = profile.competitor_limit
        context["number_of_competitors"] = profile.number_of_competitors
        context["can_add_competitors"] = profile.can_add_competitors
        context["competitor_posts_limit"] = profile.competitor_posts_limit
        context["number_of_competitor_posts_generated"] = (
            profile.number_of_competitor_posts_generated
        )
        context["can_generate_competitor_posts"] = profile.can_generate_competitor_posts
        context["is_on_free_plan"] = profile.is_on_free_plan

        return context


class GeneratedBlogPostDetailView(LoginRequiredMixin, DetailView):
    model = GeneratedBlogPost
    template_name = "blog/generated_blog_post_detail.html"
    context_object_name = "generated_post"

    def get_queryset(self):
        return GeneratedBlogPost.objects.filter(
            project__profile=self.request.user.profile, project__pk=self.kwargs["project_pk"]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        generated_post = self.object
        project = generated_post.project
        profile = self.request.user.profile

        context["project"] = project
        context["has_pro_subscription"] = profile.is_on_pro_plan
        context["has_auto_submission_setting"] = AutoSubmissionSetting.objects.filter(
            project=project
        ).exists()

        # Add keyword usage info to the title suggestion
        if generated_post.title:
            # Get all project keywords with their usage status for quick lookup
            project_keywords = project.get_keywords()

            # Add keyword usage info to the suggestion
            generated_post.title_suggestion.keywords_with_usage = []
            if generated_post.title_suggestion.target_keywords:
                for keyword_text in generated_post.title_suggestion.target_keywords:
                    keyword_info = project_keywords.get(
                        keyword_text.lower(),
                        {"keyword": None, "in_use": False, "project_keyword_id": None},
                    )
                    generated_post.title_suggestion.keywords_with_usage.append(
                        {
                            "text": keyword_text,
                            "keyword": keyword_info["keyword"],
                            "in_use": keyword_info["in_use"],
                            "project_keyword_id": keyword_info["project_keyword_id"],
                        }
                    )

        return context


class CompetitorBlogPostDetailView(LoginRequiredMixin, DetailView):
    model = Competitor
    template_name = "project/competitor_blog_post_detail.html"
    context_object_name = "competitor"

    def get_queryset(self):
        return Competitor.objects.filter(
            project__profile=self.request.user.profile, project__pk=self.kwargs["project_pk"]
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        competitor = self.object
        project = competitor.project

        context["project"] = project
        context["blog_post_title"] = f"{project.name} vs. {competitor.name}: Which is Better?"

        return context


@login_required
def download_blog_post_pdf(request, project_pk, pk):
    generated_post = GeneratedBlogPost.objects.filter(
        project__profile=request.user.profile, project__pk=project_pk, pk=pk
    ).first()

    if not generated_post:
        messages.error(request, "Blog post not found.")
        return redirect("home")

    html_content = render_to_string(
        "blog/generated_blog_post_pdf.html",
        {
            "generated_post": generated_post,
            "project": generated_post.project,
        },
    )

    pdf_file = HTML(string=html_content).write_pdf()

    response = HttpResponse(pdf_file, content_type="application/pdf")
    filename = f"{generated_post.slug}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    logger.info(
        "PDF downloaded for blog post",
        blog_post_id=generated_post.id,
        project_id=generated_post.project.id,
        user_id=request.user.id,
    )

    return response


class PublishHistoryView(LoginRequiredMixin, DetailView):
    login_url = "account_login"
    model = Project
    template_name = "project/project_publish_history.html"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object

        published_posts = GeneratedBlogPost.objects.filter(project=project, posted=True).order_by(
            "-date_posted", "-updated_at"
        )

        context["published_posts"] = published_posts
        context["total_published_count"] = published_posts.count()

        return context


class ProjectDeleteView(LoginRequiredMixin, SuccessMessageMixin, DeleteView):
    login_url = "account_login"
    model = Project
    success_url = reverse_lazy("home")
    success_message = "Project deleted successfully"

    def get_queryset(self):
        return Project.objects.filter(profile=self.request.user.profile)

    def form_valid(self, form):
        project_name = self.object.name or self.object.url

        async_task(
            track_event,
            profile_id=self.request.user.profile.id,
            event_name=ANALYTICS_EVENTS.PROJECT_DELETED,
            properties={
                "project_id": self.object.id,
                "project_name": project_name,
            },
            source_function="ProjectDeleteView - form_valid",
            group="Track Event",
        )

        return super().form_valid(form)


def trigger_error(request):
    try:
        foo = 1 / 0
    except ZeroDivisionError as e:
        logger.exception("[TriggerError] Triggering zero division error", error=str(e), foo="bar")

    try:
        raise Exception("This is a test error")
    except Exception as e:
        raise e

    return foo


def changelog_view(request):
    """
    Render the CHANGELOG.md file as an HTML page.
    """
    changelog_file = Path(settings.BASE_DIR) / "CHANGELOG.md"

    with open(changelog_file, encoding="utf-8") as file:
        changelog_content = file.read()

    changelog_html = markdown.markdown(
        changelog_content, extensions=["fenced_code", "tables", "codehilite"]
    )

    context = {
        "content": changelog_html,
        "page_title": "Changelog",
    }

    return render(request, "pages/changelog.html", context)


def skill_markdown_view(request):
    """Serve the canonical agent skill markdown file at /skill.md."""
    skill_file = Path(settings.BASE_DIR) / "skill.md"

    with open(skill_file, encoding="utf-8") as file:
        skill_content = file.read()

    return HttpResponse(skill_content, content_type="text/markdown; charset=utf-8")
