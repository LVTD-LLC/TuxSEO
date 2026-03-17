import json
import random
import re
from urllib.parse import unquote, urlencode, urljoin, urlparse

import posthog
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from core.analytics import (
    EVENT_TAXONOMY_VERSION,
    get_event_definition,
    is_known_event_name,
    normalize_event_name,
)
from core.choices import (
    CompetitorPostGenerationStatus,
    ContentType,
    EmailType,
    ExecutionJobOperation,
    ExecutionJobStatus,
    ProjectPageSource,
)
from core.execution_reliability import append_job_history, build_failure_payload
from core.integration_analytics import sync_project_provider_analytics
from core.models import (
    AgentExecutionJob,
    BlogPostTitleSuggestion,
    Competitor,
    EmailSent,
    GeneratedBlogPost,
    Profile,
    Project,
    ProjectKeyword,
    ProjectPage,
)
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

SITEMAP_PATH_CANDIDATES = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemaps.xml",
    "/wp-sitemap.xml",
)


def _extract_sitemap_urls_from_robots(robots_txt: str, base_url: str) -> list[str]:
    sitemap_urls: list[str] = []

    for line in robots_txt.splitlines():
        match = re.match(r"^\s*Sitemap\s*:\s*(\S+)\s*$", line, flags=re.IGNORECASE)
        if not match:
            continue

        sitemap_url = match.group(1).strip()
        if not sitemap_url:
            continue

        if sitemap_url.startswith(("http://", "https://")):
            sitemap_urls.append(sitemap_url)
        else:
            sitemap_urls.append(urljoin(base_url, sitemap_url))

    return sitemap_urls


def _looks_like_sitemap_xml(content: bytes) -> bool:
    if not content:
        return False

    content_start = content[:5000].lstrip().lower()
    return b"<urlset" in content_start or b"<sitemapindex" in content_start


def discover_sitemap_url(project_url: str) -> tuple[str | None, str]:
    parsed_url = urlparse(project_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        return None, "invalid_project_url"

    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    candidate_urls: list[str] = []

    robots_url = urljoin(base_url, "/robots.txt")
    try:
        robots_response = requests.get(robots_url, timeout=15)
        if robots_response.ok and robots_response.text:
            candidate_urls.extend(
                _extract_sitemap_urls_from_robots(robots_response.text, base_url)
            )
    except Exception as error:
        logger.warning(
            "[Sitemap Discovery] Failed to fetch robots.txt",
            robots_url=robots_url,
            error=str(error),
        )

    candidate_urls.extend(urljoin(base_url, path) for path in SITEMAP_PATH_CANDIDATES)

    seen_candidates = set()
    unique_candidates = []
    for candidate in candidate_urls:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        unique_candidates.append(candidate)

    parse_failures = 0

    for candidate_url in unique_candidates:
        try:
            response = requests.get(candidate_url, timeout=20)
            if not response.ok:
                logger.info(
                    "[Sitemap Discovery] Candidate not accessible",
                    candidate_url=candidate_url,
                    status_code=response.status_code,
                )
                continue

            if _looks_like_sitemap_xml(response.content):
                logger.info(
                    "[Sitemap Discovery] Candidate accepted",
                    candidate_url=candidate_url,
                )
                return candidate_url, "found"

            parse_failures += 1
            logger.warning(
                "[Sitemap Discovery] Candidate fetched but does not look like sitemap XML",
                candidate_url=candidate_url,
                status_code=response.status_code,
            )
        except Exception as error:
            logger.warning(
                "[Sitemap Discovery] Candidate fetch failed",
                candidate_url=candidate_url,
                error=str(error),
            )

    if parse_failures > 0:
        return None, "parse_fail"

    return None, "not_found"


def auto_discover_and_ingest_sitemap(project_id: int):
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error("[Sitemap Discovery] Project not found", project_id=project_id)
        return f"Project {project_id} not found."

    if not project.url:
        logger.warning(
            "[Sitemap Discovery] Project URL missing",
            project_id=project_id,
            project_name=project.name,
        )
        return f"Project URL missing for {project.name}."

    if project.sitemap_url:
        logger.info(
            "[Sitemap Discovery] Project already has sitemap URL, skipping auto-discovery",
            project_id=project_id,
            project_name=project.name,
            sitemap_url=project.sitemap_url,
        )
        return f"Project {project.name} already has sitemap URL."

    logger.info(
        "[Sitemap Discovery] Starting discovery",
        project_id=project_id,
        project_name=project.name,
        project_url=project.url,
    )

    discovered_sitemap_url, discovery_status = discover_sitemap_url(project.url)

    if not discovered_sitemap_url:
        logger.info(
            "[Sitemap Discovery] Discovery finished without sitemap",
            project_id=project_id,
            project_name=project.name,
            discovery_status=discovery_status,
        )
        return f"No sitemap discovered for {project.name}. Status: {discovery_status}."

    project.sitemap_url = discovered_sitemap_url
    project.save(update_fields=["sitemap_url"])

    logger.info(
        "[Sitemap Discovery] Sitemap discovered and saved; sitemap parsing will be scheduled by signal",
        project_id=project_id,
        project_name=project.name,
        sitemap_url=discovered_sitemap_url,
    )

    return f"Discovered sitemap for {project.name}: {discovered_sitemap_url}"


def add_email_to_buttondown(email, tag):
    if not settings.BUTTONDOWN_API_KEY:
        return "Buttondown API key not found."

    data = {
        "email_address": str(email),
        "metadata": {"source": tag},
        "tags": [tag],
        "referrer_url": "https://tuxseo.app",
        "subscriber_type": "regular",
    }

    r = requests.post(
        "https://api.buttondown.email/v1/subscribers",
        headers={"Authorization": f"Token {settings.BUTTONDOWN_API_KEY}"},
        json=data,
    )

    return r.json()


def analyze_project_page(project_id: int, link: str):
    from django.core.exceptions import ValidationError

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(
            "[Analyze Project Page] Project not found",
            project_id=project_id,
        )
        return f"Project {project_id} not found"

    try:
        project_page, created = ProjectPage.objects.get_or_create(
            project=project, url=link, defaults={"source": ProjectPageSource.AI}
        )

        if created:
            logger.info(
                "[Analyze Project Page] Created new project page",
                project_id=project_id,
                project_name=project.name,
                page_url=link,
            )

            content_fetched = project_page.get_page_content()
            if not content_fetched:
                logger.warning(
                    "[Analyze Project Page] Failed to fetch page content, deleting page",
                    project_id=project_id,
                    project_name=project.name,
                    page_url=link,
                )
                project_page.delete()
                return f"Failed to fetch content for {link}, page deleted"

            project_page.analyze_content()
            logger.info(
                "[Analyze Project Page] Successfully analyzed page",
                project_id=project_id,
                project_name=project.name,
                page_url=link,
            )
        else:
            logger.info(
                "[Analyze Project Page] Page already exists, skipping",
                project_id=project_id,
                project_name=project.name,
                page_url=link,
            )

        return f"Analyzed {link} for {project.name}"

    except ValidationError as e:
        logger.error(
            "[Analyze Project Page] Invalid URL validation error",
            project_id=project_id,
            page_url=link,
            error=str(e),
        )
        # Try to find and delete the invalid page if it was created
        try:
            invalid_page = ProjectPage.objects.filter(project=project, url=link).first()
            if invalid_page:
                invalid_page.delete()
                logger.info(
                    "[Analyze Project Page] Deleted invalid page",
                    project_id=project_id,
                    page_url=link,
                )
        except Exception as delete_error:
            logger.error(
                "[Analyze Project Page] Failed to delete invalid page",
                project_id=project_id,
                page_url=link,
                error=str(delete_error),
                exc_info=True,
            )
        return f"Invalid URL validation error for {link}: {str(e)}"

    except Exception as e:
        logger.error(
            "[Analyze Project Page] Error analyzing page",
            project_id=project_id,
            page_url=link,
            error=str(e),
            exc_info=True,
        )
        return f"Error analyzing {link}: {str(e)}"


def schedule_project_page_analysis(project_id):
    project = Project.objects.get(id=project_id)

    try:
        project_links = project.get_a_list_of_links()
    except Exception as e:
        logger.error(
            "[Schedule Project Page Analysis] Failed to extract links",
            project_id=project_id,
            project_name=project.name,
            error=str(e),
            exc_info=True,
        )
        return f"Failed to extract links for {project.name}"

    if not project_links:
        logger.info(
            "[Schedule Project Page Analysis] No links found",
            project_id=project_id,
            project_name=project.name,
        )
        return f"No links found for {project.name}"

    count = 0
    for link in project_links:
        # Validate that the link is a proper URL
        if not link or not isinstance(link, str) or not link.startswith(("http://", "https://")):
            logger.warning(
                "[Schedule Project Page Analysis] Skipping invalid link",
                project_id=project_id,
                project_name=project.name,
                link=link,
            )
            continue

        async_task(
            analyze_project_page,
            project_id,
            link,
        )
        count += 1

    logger.info(
        "[Schedule Project Page Analysis] Scheduled page analysis",
        project_id=project_id,
        project_name=project.name,
        links_scheduled=count,
    )
    return f"Scheduled analysis for {count} links"


def schedule_project_competitor_analysis(project_id):
    """
    Find competitors for a project and populate their details.

    This task:
    1. Uses Perplexity to find competitors
    2. Saves competitor details (name, url, description)
    3. Fetches homepage content using Jina Reader
    4. Populates competitor name and description

    Note:
    - Competitor analysis (analyze_competitor) is not run to save costs
    - VS blog post generation is triggered manually by the user via the UI
    """
    project = Project.objects.get(id=project_id)
    competitors = project.find_competitors()
    if competitors:
        competitors = project.get_and_save_list_of_competitors()
        for competitor in competitors:
            async_task(analyze_project_competitor, competitor.id)

    # Check if we should send the setup complete email
    async_task(check_and_send_project_setup_complete_email, project_id)

    return f"Saved competitors and scheduled content fetching for {project.name}"


def analyze_project_competitor(competitor_id):
    """
    Fetch competitor homepage content and populate competitor details.

    This task:
    1. Fetches homepage content using Jina Reader (title, description, markdown)
    2. Populates competitor name/description using AI

    Note:
    - The full competitor analysis (analyze_competitor) is not run to save costs
    - VS blog post generation is triggered manually by the user via the UI
    """
    try:
        competitor = Competitor.objects.get(id=competitor_id)
    except Competitor.DoesNotExist:
        logger.error(
            "[Analyze Project Competitor] Competitor not found",
            competitor_id=competitor_id,
        )
        return f"Competitor {competitor_id} not found"

    try:
        got_content = competitor.get_page_content()

        if got_content:
            competitor.populate_name_description()
            # competitor.analyze_competitor()
            # Note: competitor.analyze_competitor() is intentionally skipped to save costs
            # VS blog post generation is now triggered manually by the user
            logger.info(
                "[Analyze Project Competitor] Competitor details populated",
                competitor_id=competitor_id,
                competitor_name=competitor.name,
            )
            return f"Got content for {competitor.name}. VS blog post can be generated manually by the user."  # noqa: E501
        else:
            logger.warning(
                "[Analyze Project Competitor] Failed to get page content",
                competitor_id=competitor_id,
                competitor_name=competitor.name,
            )
            return f"Failed to get content for competitor {competitor.name}"

    except Exception as e:
        logger.error(
            "[Analyze Project Competitor] Error processing competitor",
            competitor_id=competitor_id,
            competitor_name=competitor.name,
            error=str(e),
            exc_info=True,
        )
        return f"Error processing competitor {competitor.name}: {str(e)}"


def process_project_keywords(project_id: int):
    """
    Processes proposed keywords for a project:
    1. Creates a keyword from the project name and marks it as used.
    2. Saves proposed keywords to the Keyword model.
    3. Fetches metrics for each keyword.
    4. Associates keywords with the project.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"[KeywordProcessing] Project with id {project_id} not found.")
        return f"Project with id {project_id} not found."

    processed_count = 0
    failed_count = 0

    # First, create a keyword from the project name and mark it as used
    if project.name:
        try:
            project.save_keyword(keyword_text=project.name, use=True)
            processed_count += 1
            logger.info(
                "[KeywordProcessing] Created keyword from project name",
                project_id=project.id,
                project_name=project.name,
                keyword_text=project.name,
            )
        except Exception as e:
            failed_count += 1
            logger.error(
                "[KeywordProcessing] Error creating keyword from project name",
                error=str(e),
                exc_info=True,
                project_id=project.id,
                project_name=project.name,
            )

    if not project.proposed_keywords:
        logger.info(
            f"[KeywordProcessing] No proposed keywords for project {project.id} ({project.name})."
        )
    else:
        keyword_strings = [kw.strip() for kw in project.proposed_keywords.split(",") if kw.strip()]

        for keyword_str in keyword_strings:
            try:
                project.save_keyword(keyword_str)
                processed_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(
                    "[KeywordProcessing] Error processing keyword",
                    error=str(e),
                    exc_info=True,
                    project_id=project.id,
                    keyword_text=keyword_str,
                )

    logger.info(
        "Keyword Processing Complete",
        project_id=project.id,
        project_name=project.name,
        processed_count=processed_count,
        failed_count=failed_count,
    )

    async_task(get_and_save_related_keywords, project_id, group="Get Related Keywords")
    async_task(get_and_save_pasf_keywords, project_id, group="Get PASF Keywords")

    # Check if we should send the setup complete email
    async_task(check_and_send_project_setup_complete_email, project_id)

    return f"""
    Keyword processing for project {project.name} (ID: {project.id})
    Processed {processed_count} keywords
    Failed: {failed_count}
    """


def check_and_send_project_setup_complete_email(project_id: int):
    """
    Check if all project setup conditions are met and send the setup complete email if so.
    This function is idempotent - it will only send the email once per project.
    Uses atomic transaction with get_or_create to prevent race conditions.
    """
    from allauth.account.models import EmailAddress
    from django.db import transaction

    logger.info(
        "[Check Project Setup Complete] Checking and sending project setup complete email",
        project_id=project_id,
    )

    try:
        project = Project.objects.select_related("profile", "profile__user").get(id=project_id)
    except Project.DoesNotExist:
        logger.error(
            "[Check Project Setup Complete] Project not found",
            project_id=project_id,
        )
        return f"Project {project_id} not found"

    profile = project.profile

    # Check if email is verified
    email_address = EmailAddress.objects.filter(user=profile.user, email=profile.user.email).first()

    if not email_address:
        logger.warning(
            "[Check Project Setup Complete] Email not found",
            project_id=project_id,
            project_name=project.name,
            user=profile.user,
        )
        return

    # Check if project has been analyzed
    if not project.date_analyzed:
        logger.warning(
            "[Check Project Setup Complete] Project not analyzed",
            project_id=project_id,
            project_name=project.name,
        )
        return f"Project {project_id} not analyzed"

    # Check if project has blog post title suggestions
    blog_post_suggestions_count = project.blog_post_title_suggestions.count()
    if blog_post_suggestions_count == 0:
        logger.warning(
            "[Check Project Setup Complete] No blog post title suggestions found",
            project_id=project_id,
            project_name=project.name,
        )
        return

    # Check if project has keywords
    keywords_count = project.project_keywords.count()
    if keywords_count == 0:
        logger.warning(
            "[Check Project Setup Complete] No keywords found",
            project_id=project_id,
            project_name=project.name,
        )
        return

    # Check if project has competitors
    competitors_count = project.competitors.count()
    if competitors_count == 0:
        logger.warning(
            "[Check Project Setup Complete] No competitors found",
            project_id=project_id,
            project_name=project.name,
        )
        return

    # Use atomic transaction with get_or_create to prevent race conditions
    # This ensures only one task can successfully create the EmailSent record
    with transaction.atomic():
        email_sent, created = EmailSent.objects.get_or_create(
            profile=profile,
            email_type=EmailType.PROJECT_SETUP_COMPLETE,
            defaults={"email_address": profile.user.email},
        )

        if not created:
            logger.warning(
                "[Check Project Setup Complete] Email already sent, skipping",
                project_id=project_id,
                project_name=project.name,
                user_email=profile.user.email,
            )
            return

    # All conditions met and we successfully created the EmailSent record - send the email
    logger.info(
        "[Check Project Setup Complete] All conditions met, sending email",
        project_id=project_id,
        project_name=project.name,
        user_email=profile.user.email,
        blog_post_suggestions_count=blog_post_suggestions_count,
        keywords_count=keywords_count,
        competitors_count=competitors_count,
    )

    # Send the email
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse

    user = profile.user

    # Construct URLs
    pages_url = f"{settings.SITE_URL}{reverse('project_pages', kwargs={'pk': project.id})}"
    project_posts_url = (
        f"{settings.SITE_URL}{reverse('project_seo_posts', kwargs={'pk': project.id})}"
    )

    # Prepare template context
    context = {
        "user": user,
        "profile": profile,
        "project": project,
        "blog_post_suggestions_count": blog_post_suggestions_count,
        "keywords_count": keywords_count,
        "competitors_count": competitors_count,
        "pages_url": pages_url,
        "project_posts_url": project_posts_url,
    }

    try:
        # Render the MJML template
        email_content = render_to_string("emails/project_setup_complete.html", context)

        # Extract subject from the template
        subject = f"🎉 Your project {project.name} is ready!"

        # Create plain text version
        plain_text = f"""Congratulations, {user.first_name or user.username}! 🎉

You've successfully created your first project {project.name}! We've been hard at work analyzing your website and gathering insights to help you create amazing content.

Here's what we've accomplished so far:

✅ Created {blog_post_suggestions_count} Blog Post Suggestions
✅ Found {keywords_count} Keywords to consider tracking and using in posts
✅ Found {competitors_count} Competitors to learn from

💡 Pro Tip: Add a sitemap to your project so we can correctly get all your pages to insert into blog posts.

Add Sitemap: {pages_url}

Ready to generate your first blog post?

Choose from your {blog_post_suggestions_count} blog post suggestions and let our AI create a comprehensive, SEO-optimized article for you.

Generate Your First Post: {project_posts_url}

If you have any questions or need help, just reply to this email. I'm here to help!

Best regards,
- Rasul
Founder, TuxSEO

---
This email was sent by TuxSEO
"""  # noqa: E501

        # Create email with both plain text and HTML versions
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )

        # Attach the HTML version (rendered from MJML)
        email.attach_alternative(email_content, "text/html")

        # Send the email
        email.send(fail_silently=False)

        # Track the email
        async_task(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.PROJECT_SETUP_COMPLETE,
            profile=profile,
            group="Track Email Sent",
        )

        logger.info(
            "[Check Project Setup Complete] Email sent successfully",
            project_id=project_id,
            user_email=user.email,
            blog_post_suggestions_count=blog_post_suggestions_count,
            keywords_count=keywords_count,
            competitors_count=competitors_count,
        )

        return f"Email sent to {user.email}"

    except Exception as error:
        logger.error(
            "[Check Project Setup Complete] Failed to send email",
            error=str(error),
            exc_info=True,
            project_id=project_id,
            user_email=user.email if user else "unknown",
        )
        return f"Failed to send email to {user.email if user else 'unknown'}"


def generate_blog_post_suggestions(project_id: int):
    project = Project.objects.get(id=project_id)
    profile = project.profile

    if profile.reached_title_generation_limit:
        return "Title generation limit reached for free plan"

    project.generate_title_suggestions(content_type=ContentType.SHARING, num_titles=3)
    project.generate_title_suggestions(content_type=ContentType.SEO, num_titles=3)

    # Check if we should send the setup complete email
    async_task(check_and_send_project_setup_complete_email, project_id)

    return "Blog post suggestions generated"


def try_create_posthog_alias(profile_id: int, cookies: dict, source_function: str = None) -> str:
    if not settings.POSTHOG_API_KEY:
        return "PostHog API key not found."

    base_log_data = {
        "profile_id": profile_id,
        "cookies": cookies,
        "source_function": source_function,
    }

    profile = Profile.objects.get(id=profile_id)
    email = profile.user.email

    base_log_data["email"] = email
    base_log_data["profile_id"] = profile_id

    posthog_cookie = cookies.get(f"ph_{settings.POSTHOG_API_KEY}_posthog")
    if not posthog_cookie:
        logger.warning("[Try Create Posthog Alias] No PostHog cookie found.", **base_log_data)
        return f"No PostHog cookie found for profile {profile_id}."
    base_log_data["posthog_cookie"] = posthog_cookie

    logger.info("[Try Create Posthog Alias] Setting PostHog alias", **base_log_data)

    cookie_dict = json.loads(unquote(posthog_cookie))
    frontend_distinct_id = cookie_dict.get("distinct_id")

    if frontend_distinct_id:
        posthog.alias(frontend_distinct_id, email)
        posthog.alias(frontend_distinct_id, str(profile_id))

    logger.info("[Try Create Posthog Alias] Set PostHog alias", **base_log_data)


def track_event(
    profile_id: int, event_name: str, properties: dict, source_function: str = None
) -> str:
    canonical_event_name = normalize_event_name(event_name)

    base_log_data = {
        "profile_id": profile_id,
        "event_name": canonical_event_name,
        "input_event_name": event_name,
        "properties": properties,
        "source_function": source_function,
    }

    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error("[TrackEvent] Profile not found.", **base_log_data)
        return f"Profile with id {profile_id} not found."

    if canonical_event_name != event_name:
        logger.info("[TrackEvent] Normalized deprecated event name", **base_log_data)

    if not is_known_event_name(canonical_event_name):
        logger.warning("[TrackEvent] Unknown event name", **base_log_data)
        return f"Unknown event name: {canonical_event_name}"

    event_definition = get_event_definition(canonical_event_name)
    if not event_definition:
        logger.warning("[TrackEvent] Missing event definition", **base_log_data)
        return f"Missing event definition for event: {canonical_event_name}"

    if settings.POSTHOG_API_KEY:
        posthog.capture(
            profile.user.email,
            event=canonical_event_name,
            properties={
                "profile_id": profile.id,
                "email": profile.user.email,
                "current_state": profile.state,
                "event_schema_version": EVENT_TAXONOMY_VERSION,
                "event_stage": event_definition["stage"],
                **properties,
            },
        )

    logger.info("[TrackEvent] Tracked event", **base_log_data)

    return f"Tracked event {canonical_event_name} for profile {profile_id}"


def track_state_change(
    profile_id: int,
    from_state: str,
    to_state: str,
    metadata: dict = None,
    source_function: str = None,
) -> None:
    from core.models import Profile, ProfileStateTransition

    base_log_data = {
        "profile_id": profile_id,
        "from_state": from_state,
        "to_state": to_state,
        "metadata": metadata,
        "source_function": source_function,
    }

    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error("[TrackStateChange] Profile not found.", **base_log_data)
        return f"Profile with id {profile_id} not found."

    if from_state != to_state:
        logger.info("[TrackStateChange] Tracking state change", **base_log_data)
        ProfileStateTransition.objects.create(
            profile=profile,
            from_state=from_state,
            to_state=to_state,
            backup_profile_id=profile_id,
            metadata=metadata,
        )
        profile.state = to_state
        profile.save(update_fields=["state"])

    return f"Tracked state change from {from_state} to {to_state} for profile {profile_id}"


def generate_and_post_blog_post(project_id: int):
    project = Project.objects.get(id=project_id)
    profile = project.profile
    blog_post_to_post = None

    if not profile.has_auto_posting_enabled:
        return f"Auto-posting not available on {profile.product_name} plan"

    logger.info(
        "[Generate and Post Blog Post] Generating blog post for {project.name}",
        project_id=project_id,
        project_name=project.name,
    )

    # first see if there are generated blog posts that are not posted yet
    blog_posts_to_post = GeneratedBlogPost.objects.filter(project=project, posted=False)

    if blog_posts_to_post.exists():
        logger.info(
            "[Generate and Post Blog Post] Found BlogPost to posts for {project.name}",
            project_id=project_id,
            project_name=project.name,
        )
        blog_post_to_post = blog_posts_to_post.first()

    # then see if there are blog post title suggestions without generated blog posts
    if not blog_post_to_post:
        ungenerated_blog_post_suggestions = BlogPostTitleSuggestion.objects.filter(
            project=project, generated_blog_posts__isnull=True
        )
        if ungenerated_blog_post_suggestions.exists():
            logger.info(
                "[Generate and Post Blog Post] Found BlogPostTitleSuggestion to generate and post for {project.name}",  # noqa: E501
                project_id=project_id,
                project_name=project.name,
            )
            ungenerated_blog_post_suggestion = ungenerated_blog_post_suggestions.first()
            blog_post_to_post = ungenerated_blog_post_suggestion.generate_content(
                content_type=ungenerated_blog_post_suggestion.content_type
            )

    # if neither, create a new blog post title suggestion, generate the blog post
    if not blog_post_to_post:
        logger.info(
            "[Generate and Post Blog Post] No BlogPost or BlogPostTitleSuggestion found, so generating both.",  # noqa: E501
            project_id=project_id,
            project_name=project.name,
        )
        content_type = random.choice([choice[0] for choice in ContentType.choices])
        suggestions = project.generate_title_suggestions(content_type=content_type, num_titles=1)
        blog_post_to_post = suggestions[0].generate_content(
            content_type=suggestions[0].content_type
        )

    # once you have the generated blog post, submit it to the endpoint
    if blog_post_to_post:
        logger.info(
            "[Generate and Post Blog Post] Submitting blog post to endpoint",
            project_id=project_id,
            project_name=project.name,
            blog_post_title=blog_post_to_post.title,
        )
        result = blog_post_to_post.submit_blog_post_to_endpoint()
        if result is True:
            blog_post_to_post.posted = True
            blog_post_to_post.date_posted = timezone.now()
            blog_post_to_post.save(update_fields=["posted", "date_posted"])
            return f"Posted blog post for {project.name}"
        else:
            return f"Failed to post blog post for {project.name}."

    else:
        logger.error(
            "[Generate and Post Blog Post] No blog post to post. This should not happen.",
            project_id=project_id,
            project_name=project.name,
        )
        return f"No blog post to post for {project.name}."


def save_title_suggestion_keywords(title_suggestion_id: int):
    title_suggestion = BlogPostTitleSuggestion.objects.get(id=title_suggestion_id)
    project = title_suggestion.project

    if not title_suggestion.target_keywords or not title_suggestion.project:
        logger.warning(
            "[Save Title Suggestion Keywords] No target keywords or project found",
            title_suggestion_id=title_suggestion_id,
            has_keywords=bool(title_suggestion.target_keywords),
            has_project=bool(title_suggestion.project),
        )
        return "No keywords or project to save"

    saved_keywords_count = 0
    for keyword_text in title_suggestion.target_keywords:
        if keyword_text and keyword_text.strip():
            project.save_keyword(keyword_text.strip())
            saved_keywords_count += 1

    logger.info(
        "[Save Title Suggestion Keywords] Successfully saved keywords",
        title_suggestion_id=title_suggestion_id,
        project_id=title_suggestion.project_id,
        project_name=title_suggestion.project.name,
        saved_keywords_count=saved_keywords_count,
        total_keywords=len(title_suggestion.target_keywords),
    )

    return f"Saved {saved_keywords_count} keywords for project {title_suggestion.project.name}"


def get_and_save_related_keywords(
    project_id: int,
    limit: int = 10,
    num_related_keywords: int = 5,
    volume_threshold: int = 10000,
):
    """
    Expands project keywords by finding and saving related keywords from Keywords Everywhere API.

    Process:
    1. Finds high-volume keywords (>volume_threshold) that haven't been processed yet
    2. For each keyword, calls Keywords Everywhere API to get related keywords
    3. Saves each related keyword to database with metrics and project association
    4. Marks parent keyword as processed to avoid duplicate API calls

    Args:
        project_id: ID of the project to process keywords for
        limit: Maximum number of parent keywords to process (default: 10)
        num_related_keywords: Number of related keywords to request per keyword (default: 5)
        volume_threshold: Minimum search volume for parent keywords (default: 10000)

    Returns:
        String summary of processing results
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"[GetRelatedKeywords] Project {project_id} not found.")
        return f"Project {project_id} not found."

    keywords_to_process = ProjectKeyword.objects.filter(
        project=project,
        keyword__volume__gt=volume_threshold,
        keyword__volume__isnull=False,
        keyword__got_related_keywords=False,
    ).select_related("keyword")[:limit]

    if not keywords_to_process.exists():
        return f"No unprocessed high-volume keywords found for {project.name}."

    stats = {
        "processed": 0,
        "failed": 0,
        "total": keywords_to_process.count(),
        "credits_used": 0,
        "related_found": 0,
        "related_saved": 0,
    }

    logger.info(f"[GetRelatedKeywords] Processing {stats['total']} keywords for {project.name}")

    api_url = "https://api.keywordseverywhere.com/v1/get_related_keywords"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.KEYWORDS_EVERYWHERE_API_KEY}",
    }

    for project_keyword in keywords_to_process:
        keyword = project_keyword.keyword

        try:
            response = requests.post(
                api_url,
                data={"keyword": keyword.keyword_text, "num": num_related_keywords},
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                related_keywords = data.get("data", [])
                stats["credits_used"] += data.get("credits_consumed", 0)
                stats["related_found"] += len(related_keywords)
                stats["processed"] += 1

                for keyword_text in related_keywords:
                    if keyword_text and keyword_text.strip():
                        try:
                            project.save_keyword(keyword_text.strip())
                            stats["related_saved"] += 1
                        except Exception as e:
                            logger.error(
                                "[GetRelatedKeywords] Failed to save keyword",
                                keyword_text=keyword_text,
                                error=str(e),
                                exc_info=True,
                            )

                keyword.got_related_keywords = True
                keyword.save(update_fields=["got_related_keywords"])

            else:
                stats["failed"] += 1
                logger.warning(
                    "[GetRelatedKeywords] API error for Keyword",
                    keyword_text=keyword.keyword_text,
                    response_status_code=response.status_code,
                    exc_info=True,
                )

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                "[GetRelatedKeywords] Error processing Keyword",
                keyword_text=keyword.keyword_text,
                error=str(e),
                exc_info=True,
            )

    logger.info(
        "[GetRelatedKeywords] Completed",
        project_id=project_id,
        project_name=project.name,
        processed=stats["processed"],
        total=stats["total"],
        failed=stats["failed"],
        credits_used=stats["credits_used"],
        related_found=stats["related_found"],
        related_saved=stats["related_saved"],
    )

    return f"""Related Keywords Processing Results for {project.name}:
    Keywords processed: {stats["processed"]}/{stats["total"]}
    Failed: {stats["failed"]}
    API credits used: {stats["credits_used"]}
    Related keywords found: {stats["related_found"]}
    Related keywords saved: {stats["related_saved"]}"""


def get_and_save_pasf_keywords(
    project_id: int,
    limit: int = 10,
    num_pasf_keywords: int = 5,
    volume_threshold: int = 10000,
):
    """
    Expands project keywords by finding and saving "People Also Search For"
    keywords from Keywords Everywhere API.

    Process:
    1. Finds high-volume keywords (>volume_threshold) that haven't been processed for PASF yet
    2. For each keyword, calls Keywords Everywhere PASF API to get related search queries
    3. Saves each PASF keyword to database with metrics and project association
    4. Marks parent keyword as processed to avoid duplicate API calls

    Args:
        project_id: ID of the project to process keywords for
        limit: Maximum number of parent keywords to process (default: 10)
        num_pasf_keywords: Number of PASF keywords to request per keyword (default: 5)
        volume_threshold: Minimum search volume for parent keywords (default: 10000)

    Returns:
        String summary of processing results
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"[GetPASFKeywords] Project {project_id} not found.")
        return f"Project {project_id} not found."

    keywords_to_process = ProjectKeyword.objects.filter(
        project=project,
        keyword__volume__gt=volume_threshold,
        keyword__volume__isnull=False,
        keyword__got_people_also_search_for_keywords=False,
    ).select_related("keyword")[:limit]

    if not keywords_to_process.exists():
        return f"No unprocessed high-volume keywords found for PASF processing in {project.name}."

    stats = {
        "processed": 0,
        "failed": 0,
        "total": keywords_to_process.count(),
        "credits_used": 0,
        "pasf_found": 0,
        "pasf_saved": 0,
    }

    logger.info(f"[GetPASFKeywords] Processing {stats['total']} keywords for {project.name}")

    api_url = "https://api.keywordseverywhere.com/v1/get_pasf_keywords"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.KEYWORDS_EVERYWHERE_API_KEY}",
    }

    for project_keyword in keywords_to_process:
        keyword = project_keyword.keyword

        try:
            response = requests.post(
                api_url,
                data={"keyword": keyword.keyword_text, "num": num_pasf_keywords},
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                pasf_keywords = data.get("data", [])
                stats["credits_used"] += data.get("credits_consumed", 0)
                stats["pasf_found"] += len(pasf_keywords)
                stats["processed"] += 1

                for keyword_text in pasf_keywords:
                    if keyword_text and keyword_text.strip():
                        try:
                            project.save_keyword(keyword_text.strip())
                            stats["pasf_saved"] += 1
                        except Exception as e:
                            logger.error(
                                "[GetPASFKeywords] Failed to save keyword",
                                keyword_text=keyword_text,
                                error=str(e),
                                exc_info=True,
                            )

                keyword.got_people_also_search_for_keywords = True
                keyword.save(update_fields=["got_people_also_search_for_keywords"])

            else:
                stats["failed"] += 1
                logger.warning(
                    "[GetPASFKeywords] API error for Keyword",
                    keyword_text=keyword.keyword_text,
                    response_status_code=response.status_code,
                    response_content=response.content.decode("utf-8")
                    if response.content
                    else "No content",
                    exc_info=True,
                )

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                "[GetPASFKeywords] Error processing Keyword",
                keyword_text=keyword.keyword_text,
                error=str(e),
                exc_info=True,
            )

    logger.info(
        f"[GetPASFKeywords] Completed: {stats['processed']}/{stats['total']} keywords processed"
    )

    return f"""PASF Keywords Processing Results for {project.name}:
    Keywords processed: {stats["processed"]}/{stats["total"]}
    Failed: {stats["failed"]}
    API credits used: {stats["credits_used"]}
    PASF keywords found: {stats["pasf_found"]}
    PASF keywords saved: {stats["pasf_saved"]}"""


def parse_sitemap_and_save_urls(project_id: int):
    """
    Parse the project's sitemap and save all URLs as ProjectPage records with SITEMAP source.
    This task is called immediately when a user adds a sitemap URL.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"[Parse Sitemap] Project {project_id} not found.")
        return f"Project {project_id} not found."

    if not project.sitemap_url:
        logger.warning(
            "[Parse Sitemap] No sitemap URL found for project",
            project_id=project_id,
            project_name=project.name,
        )
        return f"No sitemap URL found for project {project.name}."

    logger.info(
        "[Parse Sitemap] Starting sitemap parsing",
        project_id=project_id,
        project_name=project.name,
        sitemap_url=project.sitemap_url,
    )

    try:
        response = requests.get(project.sitemap_url, timeout=30)
        response.raise_for_status()

        # Parse XML content
        import xml.etree.ElementTree as ET

        root = ET.fromstring(response.content)

        # Handle both sitemap formats: standard sitemap and sitemap index
        namespace = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        urls_found = []

        # Check if this is a sitemap index
        sitemap_locs = root.findall(".//ns:sitemap/ns:loc", namespace)
        if sitemap_locs:
            # This is a sitemap index, fetch each sitemap
            for sitemap_loc in sitemap_locs:
                sitemap_url = sitemap_loc.text
                try:
                    sitemap_response = requests.get(sitemap_url, timeout=30)
                    sitemap_response.raise_for_status()
                    sitemap_root = ET.fromstring(sitemap_response.content)
                    url_locs = sitemap_root.findall(".//ns:url/ns:loc", namespace)
                    urls_found.extend([loc.text for loc in url_locs if loc.text])
                except Exception as e:
                    logger.warning(
                        "[Parse Sitemap] Failed to fetch nested sitemap",
                        sitemap_url=sitemap_url,
                        error=str(e),
                    )
        else:
            # This is a regular sitemap
            url_locs = root.findall(".//ns:url/ns:loc", namespace)
            urls_found.extend([loc.text for loc in url_locs if loc.text])

        # Save URLs to database
        created_count = 0
        existing_count = 0

        for url in urls_found:
            project_page, created = ProjectPage.objects.get_or_create(
                project=project, url=url, defaults={"source": ProjectPageSource.SITEMAP}
            )
            if created:
                created_count += 1
            else:
                existing_count += 1

        logger.info(
            "[Parse Sitemap] Completed sitemap parsing",
            project_id=project_id,
            project_name=project.name,
            total_urls=len(urls_found),
            created_count=created_count,
            existing_count=existing_count,
        )

        # Schedule analysis of first 10 unanalyzed pages
        async_task(
            "core.tasks.analyze_sitemap_pages",
            project_id,
            group="Analyze Sitemap Pages",
        )

        return f"""Sitemap parsing completed for {project.name}:
        Total URLs found: {len(urls_found)}
        New pages: {created_count}
        Existing pages: {existing_count}"""

    except requests.RequestException as e:
        logger.error(
            "[Parse Sitemap] Request error",
            project_id=project_id,
            project_name=project.name,
            sitemap_url=project.sitemap_url,
            error=str(e),
            exc_info=True,
        )
        return f"Failed to fetch sitemap for {project.name}: {str(e)}"
    except Exception as e:
        logger.error(
            "[Parse Sitemap] Unexpected error",
            project_id=project_id,
            project_name=project.name,
            error=str(e),
            exc_info=True,
        )
        return f"Error parsing sitemap for {project.name}: {str(e)}"


def analyze_sitemap_pages(project_id: int, limit: int = 10):
    """
    Analyze up to 'limit' unanalyzed project pages from sitemap for a project.
    This fetches page content and generates summaries.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        logger.error(f"[Analyze Sitemap Pages] Project {project_id} not found.")
        return f"Project {project_id} not found."

    # Get unanalyzed pages from sitemap source (pages without date_analyzed)
    unanalyzed_pages = ProjectPage.objects.filter(
        project=project,
        source=ProjectPageSource.SITEMAP,
        date_analyzed__isnull=True,
    ).order_by("created_at")[:limit]

    if not unanalyzed_pages.exists():
        logger.info(
            "[Analyze Sitemap Pages] No unanalyzed pages found",
            project_id=project_id,
            project_name=project.name,
        )
        return f"No unanalyzed sitemap pages found for {project.name}."

    stats = {
        "total": unanalyzed_pages.count(),
        "analyzed": 0,
        "failed": 0,
    }

    logger.info(
        "[Analyze Sitemap Pages] Starting analysis",
        project_id=project_id,
        project_name=project.name,
        pages_to_analyze=stats["total"],
    )

    for project_page in unanalyzed_pages:
        try:
            # Fetch page content
            content_fetched = project_page.get_page_content()

            if content_fetched:
                # Analyze and summarize
                project_page.analyze_content()
                stats["analyzed"] += 1
                logger.info(
                    "[Analyze Sitemap Pages] Page analyzed successfully",
                    project_id=project_id,
                    project_page_id=project_page.id,
                    url=project_page.url,
                )
            else:
                stats["failed"] += 1
                logger.warning(
                    "[Analyze Sitemap Pages] Failed to fetch content, deleting page",
                    project_id=project_id,
                    project_page_id=project_page.id,
                    url=project_page.url,
                )
                # Delete the page if we can't fetch content
                project_page.delete()

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                "[Analyze Sitemap Pages] Error analyzing page, deleting",
                project_id=project_id,
                project_page_id=project_page.id,
                url=project_page.url,
                error=str(e),
                exc_info=True,
            )
            # Delete pages that cause errors during analysis
            try:
                project_page.delete()
            except Exception as delete_error:
                logger.error(
                    "[Analyze Sitemap Pages] Failed to delete page after analysis error",
                    project_id=project_id,
                    project_page_id=project_page.id,
                    url=project_page.url,
                    error=str(delete_error),
                    exc_info=True,
                )

    logger.info(
        "[Analyze Sitemap Pages] Completed analysis",
        project_id=project_id,
        project_name=project.name,
        total=stats["total"],
        analyzed=stats["analyzed"],
        failed=stats["failed"],
    )

    return f"""Sitemap page analysis for {project.name}:
    Pages analyzed: {stats["analyzed"]}/{stats["total"]}
    Failed: {stats["failed"]}"""


def generate_og_image_for_blog_post(blog_post_id: int):
    """
    Generate an Open Graph image for a blog post using Replicate flux-schnell model.
    This task is automatically triggered after blog post generation.
    Uses the project's og_image_style setting to customize the visual style.
    """
    try:
        generated_post = GeneratedBlogPost.objects.get(id=blog_post_id)
    except GeneratedBlogPost.DoesNotExist:
        logger.error(
            "[GenerateOGImage] Blog post not found",
            blog_post_id=blog_post_id,
        )
        return f"Blog post {blog_post_id} not found"

    if not settings.REPLICATE_API_TOKEN:
        logger.error(
            "[GenerateOGImage] Replicate API token not configured",
            blog_post_id=blog_post_id,
            project_id=generated_post.project_id,
        )
        return "Image generation service is not configured"

    success, message = generated_post.generate_og_image()
    return message


def track_email_sent(email_address: str, email_type: EmailType, profile: Profile = None):
    """
    Track sent emails by creating EmailSent records.
    """
    try:
        email_sent = EmailSent.objects.create(
            email_address=email_address, email_type=email_type, profile=profile
        )
        logger.info(
            "[Track Email Sent] Email tracked successfully",
            email_address=email_address,
            email_type=email_type,
            profile_id=profile.id if profile else None,
            email_sent_id=email_sent.id,
        )
        return email_sent
    except Exception as e:
        logger.error(
            "[Track Email Sent] Failed to track email",
            email_address=email_address,
            email_type=email_type,
            error=str(e),
            exc_info=True,
        )
        return None


def send_blog_post_ready_email(blog_post_id: int):
    """
    Send an email notification when a blog post is ready.
    Uses MJML template for responsive, professional email design.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse

    try:
        blog_post = GeneratedBlogPost.objects.select_related(
            "project", "project__profile", "project__profile__user"
        ).get(id=blog_post_id)
    except GeneratedBlogPost.DoesNotExist:
        logger.error(
            "[Send Blog Post Ready Email] Blog post not found",
            blog_post_id=blog_post_id,
        )
        return f"Blog post {blog_post_id} not found"

    profile = blog_post.project.profile
    user = profile.user
    project = blog_post.project

    # Check if this is the first blog post ready email sent to this profile
    is_first_blog_post_email = not EmailSent.objects.filter(
        profile=profile, email_type=EmailType.BLOG_POST_READY
    ).exists()

    # Check if project already has a sitemap
    has_sitemap = bool(project.sitemap_url and project.sitemap_url.strip())

    # Only show sitemap nudge if it's the first email AND project doesn't have a sitemap
    show_sitemap_nudge = is_first_blog_post_email and not has_sitemap

    # Construct URLs
    blog_post_url = f"{settings.SITE_URL}/project/{project.id}/post/{blog_post.id}/"
    pages_url = f"{settings.SITE_URL}{reverse('project_pages', kwargs={'pk': project.id})}"

    # Prepare template context
    context = {
        "blog_post": blog_post,
        "project": project,
        "user": user,
        "blog_post_url": blog_post_url,
        "pages_url": pages_url,
        "is_first_blog_post_email": is_first_blog_post_email,
        "show_sitemap_nudge": show_sitemap_nudge,
    }

    try:
        # Render the MJML template (includes subject, plain text, and HTML)
        email_content = render_to_string("emails/blog_post_ready.html", context)

        # Extract subject from the template
        subject = f"Your blog post is ready: {blog_post.title}"

        # Create plain text version (fallback for email clients that don't support HTML)
        plain_text = f"""Hi there!

Great news! Your blog post "{blog_post.title}" for {project.name} is ready.

We've completed extensive research and generated a comprehensive blog post for you.

View your blog post here:
{blog_post_url}

What's next?
- Review and edit the content if needed
- Post it to your blog with one click
- Generate more content for your project"""

        if show_sitemap_nudge:
            plain_text += f"""

💡 Pro Tip: Add a sitemap to your project so we can correctly get all your pages to insert into blog posts.

Add Sitemap: {pages_url}"""  # noqa: E501

        plain_text += """

Happy blogging!
- The TuxSEO Team

---
If you have any questions or feedback, just reply to this email.
"""  # noqa: E501

        # Create email with both plain text and HTML versions
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )

        # Attach the HTML version (rendered from MJML)
        email.attach_alternative(email_content, "text/html")

        # Send the email
        email.send(fail_silently=False)

        # Track the email
        async_task(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.BLOG_POST_READY,
            profile=profile,
            group="Track Email Sent",
        )

        logger.info(
            "[Send Blog Post Ready Email] Email sent successfully",
            blog_post_id=blog_post_id,
            user_email=user.email,
            project_id=project.id,
        )

        return f"Email sent to {user.email}"

    except Exception as error:
        logger.error(
            "[Send Blog Post Ready Email] Failed to send email",
            error=str(error),
            exc_info=True,
            blog_post_id=blog_post_id,
            user_email=user.email if user else "unknown",
        )


def send_feedback_request_email(profile_id: int):
    """
    Send a feedback request email to a user profile.
    Asks about their experience with TuxSEO and Black Friday upgrade offer.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse

    try:
        profile = Profile.objects.select_related("user").get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(
            "[Send Feedback Request Email] Profile not found",
            profile_id=profile_id,
        )
        return f"Profile {profile_id} not found"

    user = profile.user

    # Check if this profile has already received a feedback request email
    from core.models import EmailSent

    if EmailSent.objects.filter(profile=profile, email_type=EmailType.FEEDBACK_REQUEST).exists():
        logger.info(
            "[Send Feedback Request Email] Email already sent to this profile, skipping",
            profile_id=profile_id,
            user_email=user.email,
        )
        return f"Email already sent to {user.email}, skipping"

    # Construct the pricing URL
    pricing_url = f"{settings.SITE_URL}{reverse('pricing')}"

    # Prepare template context
    context = {
        "user": user,
        "profile": profile,
        "pricing_url": pricing_url,
    }

    try:
        # Render the MJML template
        email_content = render_to_string("emails/feedback_request.html", context)

        # Extract subject from the template
        subject = "I'd love your feedback on TuxSEO"

        # Create plain text version
        plain_text = f"""Hi {user.first_name or user.username}!

My name is Rasul, and I'm the founder of TuxSEO. I hope you're enjoying the product! I'm constantly working to improve it, and your feedback would be incredibly valuable to me.

I'd love to hear from you about:
• How are you finding TuxSEO? (ease of usage, quality of content generated)
• What's working well for you?
• What could I improve?

🎉 Black Friday Special Offer

I'm also running a special Black Friday promotion! Would you consider upgrading to Pro with my exclusive discount?

Use code BF2025-85OFF for 85% off your subscription.

If you're not interested in upgrading right now, I'd love to know why. Your feedback helps me understand what features matter most to you.

View Pricing & Upgrade: {pricing_url}

Please reply to this email with your feedback. I read every response and use your input to make TuxSEO better.

Thank you for being part of the TuxSEO community!
- Rasul

---
Just reply to this email to share your feedback.
"""  # noqa: E501

        # Create email with both plain text and HTML versions
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )

        # Attach the HTML version (rendered from MJML)
        email.attach_alternative(email_content, "text/html")

        # Send the email
        email.send(fail_silently=False)

        # Track the email
        async_task(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.FEEDBACK_REQUEST,
            profile=profile,
            group="Track Email Sent",
        )

        logger.info(
            "[Send Feedback Request Email] Email sent successfully",
            profile_id=profile_id,
            user_email=user.email,
        )

        return f"Email sent to {user.email}"

    except Exception as error:
        logger.error(
            "[Send Feedback Request Email] Failed to send email",
            error=str(error),
            exc_info=True,
            profile_id=profile_id,
            user_email=user.email if user else "unknown",
        )
        return f"Failed to send email to {user.email if user else 'unknown'}"


def send_project_feedback_checkin_email(profile_id: int):
    """
    Send a plain-text feedback check-in email to recently registered users
    who have created at least one project.
    """
    from django.core.mail import EmailMessage

    try:
        profile = Profile.objects.select_related("user").get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(
            "[Send Project Feedback Check-in Email] Profile not found",
            profile_id=profile_id,
        )
        return f"Profile {profile_id} not found"

    user = profile.user

    if EmailSent.objects.filter(
        profile=profile, email_type=EmailType.PROJECT_FEEDBACK_CHECKIN
    ).exists():
        logger.info(
            "[Send Project Feedback Check-in Email] Email already sent to this profile, skipping",
            profile_id=profile_id,
            user_email=user.email,
        )
        return f"Email already sent to {user.email}, skipping"

    if not profile.projects.exists():
        logger.info(
            "[Send Project Feedback Check-in Email] Profile has no projects, skipping",
            profile_id=profile_id,
            user_email=user.email,
        )
        return f"Profile {profile_id} has no projects, skipping"

    recipient_name = user.first_name or user.username

    subject = "Quick check-in from Rasul at TuxSEO"
    plain_text = f"""Hi {recipient_name}!

I wanted to quickly check in and see how TuxSEO is going for you.

- Are you finding the product useful so far?
- Do you have any suggestions for improvement?
- Do you need help with setup or content strategy?

If it would be useful, I'm happy to jump on a quick call and help.

Just reply to this email and I'll personally get back to you.

Best,
Rasul
rasul@tuxseo.com
"""

    try:
        email = EmailMessage(
            subject=subject,
            body=plain_text,
            from_email="rasul@tuxseo.com",
            to=[user.email],
        )
        email.send(fail_silently=False)

        async_task(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.PROJECT_FEEDBACK_CHECKIN,
            profile=profile,
            group="Track Email Sent",
        )

        logger.info(
            "[Send Project Feedback Check-in Email] Email sent successfully",
            profile_id=profile_id,
            user_email=user.email,
        )

        return f"Email sent to {user.email}"

    except Exception as error:
        logger.error(
            "[Send Project Feedback Check-in Email] Failed to send email",
            error=str(error),
            exc_info=True,
            profile_id=profile_id,
            user_email=user.email if user else "unknown",
        )
        return f"Failed to send email to {user.email if user else 'unknown'}"


def send_create_project_reminder_email(profile_id: int):
    """
    Send a reminder email to a profile who has verified their email
    but hasn't created a project yet.
    Encourages them to create their first project.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse

    try:
        profile = Profile.objects.select_related("user").get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error(
            "[Send Create Project Reminder Email] Profile not found",
            profile_id=profile_id,
        )
        return f"Profile {profile_id} not found"

    user = profile.user

    # Check if this profile has already received this email
    if EmailSent.objects.filter(
        profile=profile, email_type=EmailType.CREATE_PROJECT_REMINDER
    ).exists():
        logger.info(
            "[Send Create Project Reminder Email] Email already sent to this profile, skipping",
            profile_id=profile_id,
            user_email=user.email,
        )
        return f"Email already sent to {user.email}, skipping"

    # Double-check that profile has no projects
    if profile.projects.exists():
        logger.info(
            "[Send Create Project Reminder Email] Profile has projects, skipping",
            profile_id=profile_id,
            user_email=user.email,
        )
        return f"Profile {profile_id} has projects, skipping"

    # Construct URLs with onboarding flag
    home_url = f"{settings.SITE_URL}{reverse('home')}?{urlencode({'welcome': 'true'})}"

    # Prepare template context
    context = {
        "user": user,
        "profile": profile,
        "home_url": home_url,
    }

    try:
        # Render the MJML template
        email_content = render_to_string("emails/create_project_reminder.html", context)

        # Extract subject from the template
        subject = "Ready to create your first project?"

        # Create plain text version
        plain_text = f"""Hi {user.first_name or user.username}!

Thanks for verifying your email! I noticed you haven't created your first project yet.

TuxSEO helps you generate SEO-optimized blog posts by analyzing your website and competitors. Here's how easy it is to get started:

1. Add your website URL
2. Let TuxSEO analyze your content
3. Get AI-generated blog post suggestions tailored to your site

Create your first project: {home_url}

If you have any questions or need help getting started, just reply to this email. I'm here to help!

Best regards,
- Rasul
Founder, TuxSEO

---
Ready to get started? {home_url}
"""  # noqa: E501

        # Create email with both plain text and HTML versions
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )

        # Attach the HTML version (rendered from MJML)
        email.attach_alternative(email_content, "text/html")

        # Send the email
        email.send(fail_silently=False)

        # Track the email
        async_task(
            "core.tasks.track_email_sent",
            email_address=user.email,
            email_type=EmailType.CREATE_PROJECT_REMINDER,
            profile=profile,
            group="Track Email Sent",
        )

        logger.info(
            "[Send Create Project Reminder Email] Email sent successfully",
            profile_id=profile_id,
            user_email=user.email,
        )

        return f"Email sent to {user.email}"

    except Exception as error:
        logger.error(
            "[Send Create Project Reminder Email] Failed to send email",
            error=str(error),
            exc_info=True,
            profile_id=profile_id,
            user_email=user.email if user else "unknown",
        )
        return f"Failed to send email to {user.email if user else 'unknown'}"


def generate_competitor_vs_blog_post(competitor_id: int):
    """Generate competitor comparison post asynchronously and persist generation state."""
    try:
        competitor = Competitor.objects.select_related("project", "project__profile").get(id=competitor_id)
    except Competitor.DoesNotExist:
        logger.error(
            "[Generate Competitor VS Blog Post] Competitor not found",
            competitor_id=competitor_id,
        )
        return f"Competitor {competitor_id} not found"

    logger.info(
        "[Generate Competitor VS Blog Post] Starting generation",
        competitor_id=competitor.id,
        project_id=competitor.project_id,
        profile_id=competitor.project.profile_id if competitor.project else None,
    )

    try:
        competitor.generate_vs_blog_post()
        competitor.blog_post_generation_status = CompetitorPostGenerationStatus.COMPLETED
        competitor.blog_post_generation_completed_at = timezone.now()
        competitor.blog_post_generation_error = ""
        competitor.save(
            update_fields=[
                "blog_post_generation_status",
                "blog_post_generation_completed_at",
                "blog_post_generation_error",
            ]
        )

        logger.info(
            "[Generate Competitor VS Blog Post] Generation completed",
            competitor_id=competitor.id,
            project_id=competitor.project_id,
        )
        return f"Successfully generated competitor blog post for competitor {competitor.id}"

    except Exception as error:
        error_message = str(error)
        competitor.blog_post_generation_status = CompetitorPostGenerationStatus.FAILED
        competitor.blog_post_generation_completed_at = timezone.now()
        competitor.blog_post_generation_error = error_message[:1000]
        competitor.save(
            update_fields=[
                "blog_post_generation_status",
                "blog_post_generation_completed_at",
                "blog_post_generation_error",
            ]
        )

        logger.error(
            "[Generate Competitor VS Blog Post] Generation failed",
            competitor_id=competitor.id,
            project_id=competitor.project_id,
            error=error_message,
            exc_info=True,
        )
        return f"Failed to generate competitor blog post for competitor {competitor.id}: {error_message}"


def generate_blog_post_content(suggestion_id: int, send_email: bool = True):
    """
    Generate blog post content from a title suggestion.
    This task is queued from the API endpoint to avoid timeout issues.
    The content generation process can take 2-5 minutes to complete.

    Args:
        suggestion_id: ID of the blog post title suggestion
        send_email: Whether to send completion email (default True).
                   Set to False when called from generate_and_post_blog_post.
    """
    try:
        suggestion = BlogPostTitleSuggestion.objects.select_related(
            "project", "project__profile"
        ).get(id=suggestion_id)
    except BlogPostTitleSuggestion.DoesNotExist:
        logger.error(
            "[Generate Blog Post Content] Title suggestion not found",
            suggestion_id=suggestion_id,
        )
        return f"Title suggestion {suggestion_id} not found"

    logger.info(
        "[Generate Blog Post Content] Starting content generation",
        suggestion_id=suggestion_id,
        project_id=suggestion.project.id,
        project_name=suggestion.project.name,
        suggestion_title=suggestion.title,
        send_email=send_email,
    )

    try:
        blog_post = suggestion.generate_content(content_type=suggestion.content_type)

        if not blog_post or not blog_post.content:
            logger.error(
                "[Generate Blog Post Content] Failed to generate content",
                suggestion_id=suggestion_id,
                project_id=suggestion.project.id,
            )
            return f"Failed to generate content for suggestion {suggestion_id}"

        logger.info(
            "[Generate Blog Post Content] Content generated successfully",
            suggestion_id=suggestion_id,
            project_id=suggestion.project.id,
            blog_post_id=blog_post.id,
            content_length=len(blog_post.content),
        )

        # Send email notification if requested (i.e., manually triggered, not auto-posting)
        if send_email:
            async_task(
                "core.tasks.send_blog_post_ready_email",
                blog_post.id,
                group="Send Blog Post Ready Email",
            )
            logger.info(
                "[Generate Blog Post Content] Email notification queued",
                blog_post_id=blog_post.id,
                suggestion_id=suggestion_id,
            )

        return f"Successfully generated blog post {blog_post.id} for {suggestion.project.name}"

    except ValueError as error:
        logger.error(
            "[Generate Blog Post Content] Validation error",
            error=str(error),
            exc_info=True,
            suggestion_id=suggestion_id,
            project_id=suggestion.project.id,
        )
        return f"Validation error: {str(error)}"
    except Exception as error:
        logger.error(
            "[Generate Blog Post Content] Unexpected error",
            error=str(error),
            exc_info=True,
            suggestion_id=suggestion_id,
            project_id=suggestion.project.id if suggestion.project else None,
        )
        return f"Unexpected error: {str(error)}"


def run_agent_execution_job(job_id: int):
    try:
        with transaction.atomic():
            job = AgentExecutionJob.objects.select_for_update().select_related("project", "profile").get(
                id=job_id
            )

            if job.status == ExecutionJobStatus.CANCELED:
                logger.info("[Execution Job] Skipping canceled job", job_id=job.id)
                return f"Job {job.id} canceled before start"

            if job.status in {ExecutionJobStatus.SUCCEEDED, ExecutionJobStatus.FAILED}:
                logger.info(
                    "[Execution Job] Job already terminal",
                    job_id=job.id,
                    status=job.status,
                )
                return f"Job {job.id} already {job.status}"

            job.status = ExecutionJobStatus.RUNNING
            job.started_at = job.started_at or timezone.now()
            job.error_code = ""
            job.error_message = ""
            running_result = append_job_history(
                job.result,
                event="JOB_STARTED",
                status=ExecutionJobStatus.RUNNING,
                details={"operation": job.operation},
                at=job.started_at,
            )
            running_result.pop("failure", None)
            job.result = running_result
            job.save(
                update_fields=[
                    "status",
                    "started_at",
                    "error_code",
                    "error_message",
                    "result",
                    "updated_at",
                ]
            )

        if job.operation == ExecutionJobOperation.GENERATE_BLOG_POST:
            title_suggestion_id = int(job.payload.get("title_suggestion_id"))
            suggestion = BlogPostTitleSuggestion.objects.select_related("project", "project__profile").get(
                id=title_suggestion_id,
                project=job.project,
            )

            blog_post = suggestion.generate_content(content_type=suggestion.content_type)

            with transaction.atomic():
                latest_job = AgentExecutionJob.objects.select_for_update().get(id=job.id)
                if latest_job.status == ExecutionJobStatus.CANCELED:
                    logger.warning(
                        "[Execution Job] Job canceled while running",
                        job_id=latest_job.id,
                    )
                    return f"Job {latest_job.id} canceled while running"

                latest_job.status = ExecutionJobStatus.SUCCEEDED
                latest_job.completed_at = timezone.now()
                success_result = {
                    "blog_post_id": blog_post.id,
                    "title_suggestion_id": suggestion.id,
                }
                success_result = append_job_history(
                    success_result,
                    event="JOB_SUCCEEDED",
                    status=ExecutionJobStatus.SUCCEEDED,
                    details={"blog_post_id": blog_post.id, "title_suggestion_id": suggestion.id},
                    at=latest_job.completed_at,
                )
                success_result["rollback"] = {
                    "supported": True,
                    "state": "available",
                    "hook": f"/public-api/executions/{latest_job.id}/rollback",
                    "summary": "Reverts generated draft blog post for this execution when possible.",
                }
                latest_job.result = success_result
                latest_job.save(update_fields=["status", "completed_at", "result", "updated_at"])

            return f"Job {job.id} succeeded"

        with transaction.atomic():
            latest_job = AgentExecutionJob.objects.select_for_update().get(id=job.id)
            latest_job.status = ExecutionJobStatus.FAILED
            latest_job.completed_at = timezone.now()
            latest_job.error_code = "UNSUPPORTED_OPERATION"
            latest_job.error_message = f"Unsupported operation: {job.operation}"
            failed_result = append_job_history(
                latest_job.result,
                event="JOB_FAILED",
                status=ExecutionJobStatus.FAILED,
                details={"operation": job.operation},
                at=latest_job.completed_at,
            )
            failed_result["failure"] = build_failure_payload(
                latest_job.error_code,
                latest_job.error_message,
            )
            latest_job.result = failed_result
            latest_job.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "error_code",
                    "error_message",
                    "result",
                    "updated_at",
                ]
            )

        return f"Job {job.id} failed: unsupported operation"

    except BlogPostTitleSuggestion.DoesNotExist:
        job = AgentExecutionJob.objects.filter(id=job_id).first()
        result = append_job_history(
            job.result if job else {},
            event="JOB_FAILED",
            status=ExecutionJobStatus.FAILED,
            details={"reason": "title_suggestion_not_found"},
            at=timezone.now(),
        )
        result["failure"] = build_failure_payload(
            "TITLE_SUGGESTION_NOT_FOUND",
            "Title suggestion not found for this project",
        )
        AgentExecutionJob.objects.filter(id=job_id).update(
            status=ExecutionJobStatus.FAILED,
            completed_at=timezone.now(),
            error_code="TITLE_SUGGESTION_NOT_FOUND",
            error_message="Title suggestion not found for this project",
            result=result,
        )
        return f"Job {job_id} failed: title suggestion missing"
    except Exception as error:
        error_message = str(error)[:2000]
        job = AgentExecutionJob.objects.filter(id=job_id).first()
        result = append_job_history(
            job.result if job else {},
            event="JOB_FAILED",
            status=ExecutionJobStatus.FAILED,
            details={"reason": "unexpected_error"},
            at=timezone.now(),
        )
        result["failure"] = build_failure_payload("EXECUTION_FAILED", error_message)

        AgentExecutionJob.objects.filter(id=job_id).update(
            status=ExecutionJobStatus.FAILED,
            completed_at=timezone.now(),
            error_code="EXECUTION_FAILED",
            error_message=error_message,
            result=result,
        )
        logger.error(
            "[Execution Job] Failed",
            job_id=job_id,
            error=str(error),
            exc_info=True,
        )
        return f"Job {job_id} failed: {str(error)}"


def sync_project_integration_analytics(project_id: int, provider: str):
    """Sync one project's connected analytics provider with incremental cursor semantics."""
    result = sync_project_provider_analytics(project_id=project_id, provider=provider)

    logger.info(
        "[AnalyticsSyncTask] Provider sync finished",
        project_id=project_id,
        provider=provider,
        result_status=result.get("status"),
        rows_fetched=result.get("rows_fetched"),
        rows_upserted=result.get("rows_upserted"),
    )

    return result
