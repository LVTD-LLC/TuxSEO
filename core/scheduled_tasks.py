import calendar
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone
from django_q.tasks import async_task

from core.choices import EmailType, ProjectPageSource
from core.integration_analytics import schedule_all_connected_project_analytics_syncs
from core.models import Competitor, EmailSent, Profile, Project, ProjectPage
from core.utils import get_jina_embedding
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

User = get_user_model()


def analyze_project_sitemap_pages():
    """
    Daily scheduled task that checks all projects with sitemap URLs
    and schedules analysis for any unanalyzed pages (10 at a time per project).
    """
    projects_with_sitemaps = Project.objects.exclude(sitemap_url="")

    scheduled_count = 0

    for project in projects_with_sitemaps:
        # Check if there are unanalyzed pages from sitemap
        unanalyzed_count = ProjectPage.objects.filter(
            project=project,
            source=ProjectPageSource.SITEMAP,
            date_analyzed__isnull=True,
        ).count()

        if unanalyzed_count > 0:
            logger.info(
                "[Daily Sitemap Analysis] Scheduling analysis",
                project_id=project.id,
                project_name=project.name,
                unanalyzed_count=unanalyzed_count,
            )

            async_task(
                "core.tasks.analyze_sitemap_pages",
                project.id,
                group="Daily Sitemap Analysis",
            )
            scheduled_count += 1

    logger.info(
        "[Daily Sitemap Analysis] Completed scheduling",
        total_projects_with_sitemaps=projects_with_sitemaps.count(),
        scheduled_projects=scheduled_count,
    )

    return f"""Daily sitemap analysis check completed:
    Projects with sitemaps: {projects_with_sitemaps.count()}
    Projects scheduled for analysis: {scheduled_count}"""


def schedule_blog_post_posting():
    now = timezone.now()
    projects = Project.objects.filter(enable_automatic_post_submission=True)

    scheduled_posts = 0
    for project in projects:
        if not project.has_auto_submission_setting or not project.profile.experimental_features:
            continue

        if not project.last_posted_blog_post:
            async_task(
                "core.tasks.generate_and_post_blog_post", project.id, group="Submit Blog Post"
            )
            scheduled_posts += 1
            continue

        last_post_date = project.last_posted_blog_post.date_posted
        time_since_last_post_in_seconds = (now - last_post_date).total_seconds()

        days_in_month = calendar.monthrange(now.year, now.month)[1]
        time_between_posts_in_seconds = int(
            days_in_month
            * (24 * 60 * 60)
            / project.auto_submission_settings.latest("created_at").posts_per_month
        )

        if time_since_last_post_in_seconds > time_between_posts_in_seconds:
            logger.info(
                "[Schedule Blog Post Posting] Scheduling blog post",
                project_id=project.id,
                project_name=project.name,
            )
            async_task(
                "core.tasks.generate_and_post_blog_post", project.id, group="Submit Blog Post"
            )
            scheduled_posts += 1

    return f"Scheduled {scheduled_posts} blog posts"


def backfill_project_page_embeddings():
    """
    Scheduled task that finds all ProjectPage objects with title, description, and summary
    but no embedding, and generates embeddings for them (batch of 50 at a time).
    """
    BATCH_SIZE = 10 if settings.DEBUG else 50
    project_pages_without_embeddings = ProjectPage.objects.filter(
        embedding__isnull=True,
    ).exclude(
        Q(title__isnull=True)
        | Q(title="")
        | Q(description__isnull=True)
        | Q(description="")
        | Q(summary__isnull=True)
        | Q(summary="")
    )[:BATCH_SIZE]

    processed_count = 0
    failed_count = 0

    for project_page in project_pages_without_embeddings:
        try:
            embedding_text = (
                f"{project_page.title}\n\n{project_page.description}\n\n{project_page.summary}"
            )
            embedding = get_jina_embedding(embedding_text)

            if embedding:
                project_page.embedding = embedding
                project_page.save(update_fields=["embedding"])
                processed_count += 1
                logger.info(
                    "[Generate ProjectPage Embeddings] Successfully generated embedding",
                    project_page_id=project_page.id,
                    project_id=project_page.project_id,
                )
            else:
                failed_count += 1
                logger.warning(
                    "[Generate ProjectPage Embeddings] Failed to generate embedding",
                    project_page_id=project_page.id,
                    project_id=project_page.project_id,
                )
        except Exception as error:
            failed_count += 1
            logger.error(
                "[Generate ProjectPage Embeddings] Error generating embedding",
                error=str(error),
                exc_info=True,
                project_page_id=project_page.id,
                project_id=project_page.project_id,
            )

    remaining_count = (
        ProjectPage.objects.filter(
            embedding__isnull=True,
        )
        .exclude(
            Q(title__isnull=True)
            | Q(title="")
            | Q(description__isnull=True)
            | Q(description="")
            | Q(summary__isnull=True)
            | Q(summary="")
        )
        .count()
    )

    logger.info(
        "[Generate ProjectPage Embeddings] Batch completed",
        processed_count=processed_count,
        failed_count=failed_count,
        total_remaining=remaining_count,
    )

    return f"""ProjectPage embedding generation completed:
    Successfully processed: {processed_count}
    Failed: {failed_count}
    Remaining: {remaining_count}"""


def backfill_competitor_embeddings():
    """
    Scheduled task that finds all Competitor objects with name, description, and summary
    but no embedding, and generates embeddings for them (batch of 50 at a time).
    """
    BATCH_SIZE = 10 if settings.DEBUG else 50
    competitors_without_embeddings = Competitor.objects.filter(
        embedding__isnull=True,
    ).exclude(
        Q(name__isnull=True)
        | Q(name="")
        | Q(description__isnull=True)
        | Q(description="")
        | Q(summary__isnull=True)
        | Q(summary="")
    )[:BATCH_SIZE]

    processed_count = 0
    failed_count = 0

    for competitor in competitors_without_embeddings:
        try:
            embedding_text = (
                f"{competitor.name}\n\n{competitor.description}\n\n{competitor.summary}"
            )
            embedding = get_jina_embedding(embedding_text)

            if embedding:
                competitor.embedding = embedding
                competitor.save(update_fields=["embedding"])
                processed_count += 1
                logger.info(
                    "[Backfill Competitor Embeddings] Successfully generated embedding",
                    competitor_id=competitor.id,
                    project_id=competitor.project_id,
                )
            else:
                failed_count += 1
                logger.warning(
                    "[Backfill Competitor Embeddings] Failed to generate embedding",
                    competitor_id=competitor.id,
                    project_id=competitor.project_id,
                )
        except Exception as error:
            failed_count += 1
            logger.error(
                "[Backfill Competitor Embeddings] Error generating embedding",
                error=str(error),
                exc_info=True,
                competitor_id=competitor.id,
                project_id=competitor.project_id,
            )

    remaining_count = (
        Competitor.objects.filter(
            embedding__isnull=True,
        )
        .exclude(
            Q(name__isnull=True)
            | Q(name="")
            | Q(description__isnull=True)
            | Q(description="")
            | Q(summary__isnull=True)
            | Q(summary="")
        )
        .count()
    )

    logger.info(
        "[Backfill Competitor Embeddings] Batch completed",
        processed_count=processed_count,
        failed_count=failed_count,
        total_remaining=remaining_count,
    )

    return f"""Competitor embedding generation completed:
    Successfully processed: {processed_count}
    Failed: {failed_count}
    Remaining: {remaining_count}"""


def schedule_create_project_reminder_emails():
    """
    Daily scheduled task that finds profiles who have:
    - Registered at least 1 day ago
    - Verified their email
    - Not created any projects
    - Not received this email yet

    Schedules email sending task for each eligible profile.
    """
    now = timezone.now()
    one_day_ago = now - timedelta(days=1)

    # Get profiles that have verified emails and registered at least 1 day ago
    # Filter out profiles that have projects using annotation
    eligible_profiles = (
        Profile.objects.filter(
            user__emailaddress__verified=True,
            user__date_joined__lte=one_day_ago,
        )
        .annotate(project_count=Count("projects"))
        .filter(project_count=0)
    )

    # Exclude profiles that have already received this email
    sent_profile_ids = EmailSent.objects.filter(
        email_type=EmailType.CREATE_PROJECT_REMINDER
    ).values_list("profile_id", flat=True)

    eligible_profiles = eligible_profiles.exclude(id__in=sent_profile_ids)

    scheduled_count = 0

    for profile in eligible_profiles.select_related("user"):
        # Double-check email is verified
        email_address = EmailAddress.objects.filter(
            user=profile.user, email=profile.user.email, verified=True
        ).first()

        if not email_address:
            continue

        # Double-check no projects exist
        if profile.projects.exists():
            continue

        logger.info(
            "[Schedule Create Project Reminder] Scheduling email",
            profile_id=profile.id,
            user_email=profile.user.email,
            days_since_registration=(now - profile.user.date_joined).days,
        )

        async_task(
            "core.tasks.send_create_project_reminder_email",
            profile.id,
            group="Create Project Reminder",
        )
        scheduled_count += 1

    logger.info(
        "[Schedule Create Project Reminder] Completed scheduling",
        scheduled_profiles=scheduled_count,
    )

    return f"""Create project reminder email scheduling completed:
    Profiles scheduled: {scheduled_count}"""


def schedule_project_analytics_syncs():
    """Schedule background analytics sync tasks for all connected GA4/GSC/Plausible integrations."""
    result = schedule_all_connected_project_analytics_syncs()
    return (
        "Analytics sync scheduling completed: "
        f"scheduled={result.get('scheduled', 0)}"
    )


def schedule_project_feedback_checkin_emails():
    """
    Daily scheduled task that finds profiles who have:
    - Registered in the last 2 days
    - Verified their email
    - Created at least 1 project
    - Not received this email type yet

    Schedules a plain-text check-in email from Rasul.
    """
    now = timezone.now()
    registration_window_start_date = now - timedelta(days=2)

    eligible_profiles = (
        Profile.objects.filter(
            user__emailaddress__verified=True,
            user__date_joined__gte=registration_window_start_date,
            user__date_joined__lte=now,
        )
        .annotate(project_count=Count("projects"))
        .filter(project_count__gte=1)
        .distinct()
    )

    sent_profile_ids = EmailSent.objects.filter(
        email_type=EmailType.PROJECT_FEEDBACK_CHECKIN
    ).values_list("profile_id", flat=True)

    eligible_profiles = eligible_profiles.exclude(id__in=sent_profile_ids)

    scheduled_count = 0

    for profile in eligible_profiles.select_related("user"):
        verified_email_address = EmailAddress.objects.filter(
            user=profile.user,
            email=profile.user.email,
            verified=True,
        ).first()

        if not verified_email_address:
            continue

        if not profile.projects.exists():
            continue

        logger.info(
            "[Schedule Project Feedback Check-in] Scheduling email",
            profile_id=profile.id,
            user_email=profile.user.email,
            days_since_registration=(now - profile.user.date_joined).days,
            project_count=profile.projects.count(),
        )

        async_task(
            "core.tasks.send_project_feedback_checkin_email",
            profile.id,
            group="Project Feedback Check-in",
        )
        scheduled_count += 1

    logger.info(
        "[Schedule Project Feedback Check-in] Completed scheduling",
        scheduled_profiles=scheduled_count,
        registration_window_start_date=registration_window_start_date.isoformat(),
    )

    return f"""Project feedback check-in email scheduling completed:
    Profiles scheduled: {scheduled_count}"""
