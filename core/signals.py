from allauth.account.signals import email_confirmed, user_logged_in, user_signed_up
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django_q.tasks import async_task

from core.analytics import ANALYTICS_EVENTS
from core.models import (
    BlogPostWorkflowAuditLog,
    GeneratedBlogPost,
    LinkOpportunityAuditLog,
    Profile,
    ProfileStates,
    Project,
    ProjectPage,
)
from core.outcome_attribution import record_outcome_attribution_event
from core.tasks import add_email_to_buttondown
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        profile = Profile.objects.create(user=instance)
        profile.track_state_change(
            to_state=ProfileStates.SIGNED_UP,
        )

    if instance.id == 1:
        # Use update() to avoid triggering the signal again
        User.objects.filter(id=1).update(is_staff=True, is_superuser=True)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, "profile"):
        instance.profile.save()


@receiver(email_confirmed)
def add_email_to_buttondown_on_confirm(sender, **kwargs):
    email_address = kwargs.get("email_address")
    profile = getattr(getattr(email_address, "user", None), "profile", None)
    email = getattr(email_address, "email", "")

    if profile and email:
        async_task(
            "core.tasks.track_event",
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.EMAIL_VERIFIED,
            properties={
                "email_domain": email.split("@")[-1] if "@" in email else "",
            },
            source_function="signals.add_email_to_buttondown_on_confirm",
            group="Track Event",
        )

    logger.info(
        "Adding new user to buttondown newsletter, on email confirmation",
        kwargs=kwargs,
        sender=sender,
    )
    async_task(add_email_to_buttondown, email_address, tag="user")


@receiver(user_signed_up)
def email_confirmation_callback(sender, request, user, **kwargs):
    if "sociallogin" in kwargs:
        logger.info(
            "Adding new user to buttondown newsletter on social signup",
            kwargs=kwargs,
            sender=sender,
        )
        email = kwargs["sociallogin"].user.email
        if email:
            async_task(add_email_to_buttondown, email, tag="user")


@receiver(user_logged_in)
def capture_login_succeeded(sender, request, user, **kwargs):
    profile = getattr(user, "profile", None)
    if not profile:
        return

    auth_provider = "password"
    request_path = getattr(request, "path", "") or ""
    if "/accounts/" in request_path and "callback" in request_path:
        auth_provider = "social"

    async_task(
        "core.tasks.track_event",
        profile_id=profile.id,
        event_name=ANALYTICS_EVENTS.LOGIN_SUCCEEDED,
        properties={
            "auth_provider": auth_provider,
            "result_status": "succeeded",
        },
        source_function="signals.capture_login_succeeded",
        group="Track Event",
    )


@receiver(post_save, sender=Project)
def parse_sitemap_on_save(sender, instance, created, **kwargs):
    """
    When a project is saved with a sitemap_url, parse the sitemap and save URLs.
    Uses update_fields to check if sitemap_url was specifically updated.
    """
    update_fields = kwargs.get("update_fields")

    # Check if sitemap_url was just set or updated
    if instance.sitemap_url:
        # If update_fields is None, all fields were saved
        # If update_fields contains 'sitemap_url', it was explicitly updated
        should_parse = False

        if created and instance.sitemap_url:
            should_parse = True
        elif update_fields is None:
            # All fields updated, check if sitemap_url changed
            try:
                old_instance = Project.objects.get(pk=instance.pk)
                if old_instance.sitemap_url != instance.sitemap_url:
                    should_parse = True
            except Project.DoesNotExist:
                should_parse = True
        elif update_fields and "sitemap_url" in update_fields:
            should_parse = True

        if should_parse:
            logger.info(
                "[Parse Sitemap Signal] Scheduling sitemap parsing",
                project_id=instance.id,
                project_name=instance.name,
                sitemap_url=instance.sitemap_url,
            )
            async_task(
                "core.tasks.parse_sitemap_and_save_urls",
                instance.id,
                group="Parse Sitemap",
            )


@receiver(post_save, sender=GeneratedBlogPost)
def create_content_generation_attribution(sender, instance, created, **kwargs):
    if not created or not instance.project_id:
        return

    record_outcome_attribution_event(
        project=instance.project,
        profile=instance.project.profile,
        event_name="content.blog_post_generated",
        source_model="GeneratedBlogPost",
        source_object_id=instance.id,
        occurred_at=instance.created_at,
        metadata={"title_suggestion_id": instance.title_suggestion_id},
    )


@receiver(post_save, sender=BlogPostWorkflowAuditLog)
def create_content_publish_attribution(sender, instance, created, **kwargs):
    if not created or instance.event_type != "PUBLISHED" or not instance.project_id:
        return

    record_outcome_attribution_event(
        project=instance.project,
        profile=instance.project.profile,
        event_name="content.blog_post_published",
        source_model="BlogPostWorkflowAuditLog",
        source_object_id=instance.id,
        occurred_at=instance.created_at,
        metadata={"generated_blog_post_id": instance.generated_blog_post_id},
    )


@receiver(post_save, sender=LinkOpportunityAuditLog)
def create_distribution_attribution(sender, instance, created, **kwargs):
    if (
        not created
        or not instance.source_project_id
        or instance.phase != LinkOpportunityAuditLog.Phase.PLACEMENT
        or instance.decision != LinkOpportunityAuditLog.Decision.PLACED
    ):
        return

    record_outcome_attribution_event(
        project=instance.source_project,
        profile=instance.source_project.profile,
        event_name="distribution.link_placement",
        source_model="LinkOpportunityAuditLog",
        source_object_id=instance.id,
        occurred_at=instance.created_at,
        metadata={
            "candidate_domain": instance.candidate_domain,
            "link_source": instance.link_source,
            "generated_blog_post_id": instance.generated_blog_post_id,
        },
    )


@receiver(pre_save, sender=ProjectPage)
def remember_previous_project_page_analysis(sender, instance, **kwargs):
    previous_date_analyzed = None
    if instance.pk:
        previous_date_analyzed = (
            ProjectPage.objects.filter(pk=instance.pk).values_list("date_analyzed", flat=True).first()
        )
    instance._previous_date_analyzed = previous_date_analyzed


@receiver(post_save, sender=ProjectPage)
def create_technical_attribution(sender, instance, created, **kwargs):
    if not instance.project_id or not instance.date_analyzed:
        return

    previous_date_analyzed = getattr(instance, "_previous_date_analyzed", None)
    if not created and previous_date_analyzed is not None:
        return

    record_outcome_attribution_event(
        project=instance.project,
        profile=instance.project.profile,
        event_name="technical.page_analyzed",
        source_model="ProjectPage",
        source_object_id=instance.id,
        occurred_at=instance.date_analyzed,
        metadata={"source": instance.source, "url": instance.url},
    )
