import replicate
import requests
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django_q.models import Task
from django_q.tasks import async_task, result
from ninja import NinjaAPI

from core.abuse_prevention import enforce_verified_email_for_expensive_action
from core.analytics import ANALYTICS_EVENTS, enqueue_track_event
from core.api.auth import session_auth, superuser_api_auth
from core.api.schemas import (
    APIKeyOut,
    AddCompetitorIn,
    AddKeywordIn,
    AddKeywordOut,
    AddPricingPageIn,
    BlogPostIn,
    BlogPostOut,
    BlogPostUpdateIn,
    CompetitorAnalysisOut,
    CompetitorPostGenerationStatusOut,
    ConfirmProjectOnboardingIn,
    DeleteProjectKeywordIn,
    DeleteProjectKeywordOut,
    FixGeneratedBlogPostIn,
    FixGeneratedBlogPostOut,
    GenerateCompetitorVsTitleIn,
    GenerateCompetitorVsTitleOut,
    GeneratedContentOut,
    GenerateOGImageIn,
    GenerateOGImageOut,
    InternalBlogPostDetailOut,
    InternalBlogPostListOut,
    GenerateTitleSuggestionOut,
    GenerateTitleSuggestionsIn,
    GenerateTitleSuggestionsOut,
    GetKeywordDetailsOut,
    PostGeneratedBlogPostIn,
    PostGeneratedBlogPostOut,
    ProjectScanIn,
    ProjectScanOut,
    SubmitFeedbackIn,
    SubmitSitemapIn,
    TaskStatusOut,
    ToggleAutoSubmissionOut,
    ToggleLinkExchangeOut,
    ToggleOGImageGenerationOut,
    ToggleProjectKeywordUseIn,
    ToggleProjectKeywordUseOut,
    ToggleProjectPageAlwaysUseIn,
    ToggleProjectPageAlwaysUseOut,
    UpdateArchiveStatusIn,
    UpdateSitemapUrlIn,
    UpdateSitemapUrlOut,
    UpdateTitleScoreIn,
    UserSettingsOut,
    ValidateUrlIn,
    ValidateUrlOut,
)
from core.api_error_semantics import PlanEntitlement, evaluate_plan_entitlement, ownership_not_found_payload
from core.choices import CompetitorPostGenerationStatus, ContentType, ProjectPageType
from core.models import (
    BlogPost,
    BlogPostTitleSuggestion,
    Competitor,
    Feedback,
    GeneratedBlogPost,
    Keyword,
    Profile,
    Project,
    ProjectCustomPostType,
    ProjectKeyword,
    ProjectPage,
)
from core.publish_quality_gate import evaluate_pre_publish_quality_gate
from core.utils import download_image_from_url, generate_random_key
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

api = NinjaAPI(docs_url=None, openapi_url=None)


def pro_monthly_checkout_url() -> str:
    return reverse("user_upgrade_checkout_session", kwargs={"product_name": "Pro - Monthly"})


def get_verified_email_gate_error(profile, action_name: str) -> dict | None:
    return enforce_verified_email_for_expensive_action(profile=profile, action_name=action_name)


def get_entitlement_error(profile, entitlement: PlanEntitlement) -> dict | None:
    error = evaluate_plan_entitlement(
        profile,
        entitlement,
        upgrade_url=pro_monthly_checkout_url(),
    )
    if not error:
        return None

    if error.get("upgrade_url") and entitlement in {
        PlanEntitlement.TITLE_GENERATION,
        PlanEntitlement.CONTENT_GENERATION,
        PlanEntitlement.KEYWORD_ADD,
    }:
        upgrade_link = f"<a class='underline' href='{pro_monthly_checkout_url()}'>Upgrade to Pro</a>"
        error["message"] = f"{error['message']} {upgrade_link}"

    return error


def should_allow_unverified_first_onboarding_project(profile, project_source: str) -> bool:
    is_onboarding_modal_source = project_source == "onboarding_modal"
    has_no_existing_projects = not Project.objects.filter(profile=profile).exists()
    return is_onboarding_modal_source and has_no_existing_projects


def get_custom_post_type_for_generation(*, project: Project, post_type_id: int | None):
    if post_type_id is None:
        return None

    return ProjectCustomPostType.objects.filter(id=post_type_id, project=project).first()


def build_effective_user_prompt(*, custom_post_type, user_prompt: str) -> str:
    _ = custom_post_type
    return (user_prompt or "").strip()


@api.post("/validate-url", response=ValidateUrlOut, auth=[session_auth])
def validate_url(request: HttpRequest, data: ValidateUrlIn):
    url_to_check = data.url.strip()

    if not url_to_check:
        return {
            "status": "error",
            "reachable": False,
            "message": "URL cannot be empty",
        }

    if not url_to_check.lower().startswith(("http://", "https://")):
        return {
            "status": "error",
            "reachable": False,
            "message": "URL must start with http:// or https://",
        }

    try:
        response = requests.get(
            url_to_check,
            timeout=10.0,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

        is_reachable = response.status_code < 400

        return {
            "status": "success",
            "reachable": is_reachable,
            "message": "URL is reachable"
            if is_reachable
            else f"URL returned status code {response.status_code}",
        }

    except requests.Timeout:
        return {
            "status": "error",
            "reachable": False,
            "message": "Request timed out - website took too long to respond",
        }
    except requests.ConnectionError:
        return {
            "status": "error",
            "reachable": False,
            "message": "Cannot connect to this URL",
        }
    except Exception as error:
        logger.error(
            "[ValidateUrl] Unexpected error validating URL",
            error=str(error),
            exc_info=True,
            url=url_to_check,
        )
        return {
            "status": "error",
            "reachable": False,
            "message": "Could not validate URL",
        }


@api.get("/settings/api-key", response=APIKeyOut, auth=[session_auth])
def get_api_key(request: HttpRequest):
    return {
        "status": "success",
        "key": request.auth.key,
    }


@api.post("/settings/api-key/regenerate", response=APIKeyOut, auth=[session_auth])
def regenerate_api_key(request: HttpRequest):
    profile = request.auth
    max_attempts = 10

    for _ in range(max_attempts):
        regenerated_key = generate_random_key()
        if regenerated_key == profile.key:
            continue
        if Profile.objects.filter(key=regenerated_key).exists():
            continue

        profile.key = regenerated_key
        profile.save(update_fields=["key"])

        logger.info(
            "[API Key] Regenerated key for profile",
            profile_id=profile.id,
            user_id=profile.user_id,
        )
        return {
            "status": "success",
            "key": regenerated_key,
        }

    logger.error(
        "[API Key] Failed to regenerate unique key",
        profile_id=profile.id,
        user_id=profile.user_id,
    )
    return 500, {
        "status": "error",
        "key": "",
        "message": "Failed to regenerate API key. Please try again.",
    }


@api.post("/projects/", response=ProjectScanOut, auth=[session_auth])
def create_project(request: HttpRequest, data: ProjectScanIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "project creation")
    is_allowed_unverified_onboarding_project = should_allow_unverified_first_onboarding_project(
        profile=profile, project_source=data.source
    )
    if gate_error and not is_allowed_unverified_onboarding_project:
        return gate_error
    if gate_error and is_allowed_unverified_onboarding_project:
        logger.info(
            "[VerifiedEmailGate] Allowing unverified first onboarding project creation",
            profile_id=profile.id,
            user_id=profile.user.id,
            source=data.source,
        )

    if Project.objects.filter(profile=profile, url=data.url).exists():
        return {
            "status": "error",
            "message": "You already added this project URL",
        }

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.PROJECT_CREATE)
    if entitlement_error:
        return entitlement_error

    project = profile.get_or_create_project(url=data.url, source=data.source)

    try:
        got_content = project.get_page_content()

        analyzed_project = False
        if got_content:
            analyzed_project = project.analyze_content()
        else:
            project.delete()
            return {
                "status": "error",
                "message": "Failed to get page content",
            }

        if analyzed_project:
            try:
                async_task(
                    "core.tasks.auto_discover_and_ingest_sitemap",
                    project.id,
                    group="Discover Sitemap",
                )
            except Exception as task_error:
                logger.warning(
                    "[Create Project] Failed to enqueue sitemap auto-discovery",
                    project_id=project.id,
                    error=str(task_error),
                )

            return {
                "status": "success",
                "project_id": project.id,
                "id": project.id,
                "name": project.name,
                "type": project.get_type_display(),
                "url": project.url,
                "summary": project.summary,
                "description": project.description,
            }
        else:
            logger.error(
                "[Create Project] Failed to analyze project",
                got_content=got_content,
                analyzed_project=analyzed_project,
                project_id=project.id if project else None,
                url=data.url,
            )
            project.delete()
            return {
                "status": "error",
                "message": "Failed to analyze project",
            }

    except Exception as e:
        logger.error(
            "[Create Project] Unexpected error during project creation",
            error=str(e),
            exc_info=True,
            project_id=project.id if project else None,
            url=data.url,
            profile_id=profile.id,
        )
        if project and project.id:
            project.delete()
        return {
            "status": "error",
            "message": "An unexpected error occurred while creating the project",
        }


@api.post("/projects/{project_id}/confirm-onboarding", response={200: dict}, auth=[session_auth])
def confirm_project_onboarding(
    request: HttpRequest, project_id: int, data: ConfirmProjectOnboardingIn
):
    profile = request.auth
    project = get_object_or_404(Project, id=project_id, profile=profile)

    name = data.name.strip() or project.name or project.url
    summary = data.summary.strip()
    description = data.description.strip()

    project.name = name
    project.summary = summary
    project.description = description
    project.save(update_fields=["name", "summary", "description"])

    return {"status": "success"}


@api.post("/generate-title-suggestions", response=GenerateTitleSuggestionsOut, auth=[session_auth])
def generate_title_suggestions(request: HttpRequest, data: GenerateTitleSuggestionsIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "title generation")
    if gate_error:
        return {
            "suggestions": [],
            "suggestions_html": [],
            **gate_error,
        }

    project = get_object_or_404(Project, id=data.project_id, profile=profile)

    try:
        content_type = ContentType[data.content_type]
    except KeyError:
        return {
            "suggestions": [],
            "suggestions_html": [],
            "status": "error",
            "message": f"Invalid content type: {data.content_type}",
        }

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.TITLE_GENERATION)
    if entitlement_error:
        return {
            "suggestions": [],
            "suggestions_html": [],
            **entitlement_error,
        }

    custom_post_type = get_custom_post_type_for_generation(
        project=project,
        post_type_id=data.post_type_id,
    )
    if data.post_type_id and custom_post_type is None:
        return {
            "suggestions": [],
            "suggestions_html": [],
            "status": "error",
            "message": "Custom post type not found for this project.",
        }

    titles_to_generate = data.num_titles
    if profile.title_suggestion_limit:
        remaining_ideas = (
            profile.title_suggestion_limit - profile.number_of_title_suggestions_this_month
        )
        titles_to_generate = min(data.num_titles, remaining_ideas)

    effective_user_prompt = build_effective_user_prompt(
        custom_post_type=custom_post_type,
        user_prompt=data.user_prompt,
    )

    suggestions = project.generate_title_suggestions(
        content_type=content_type,
        num_titles=titles_to_generate,
        user_prompt=effective_user_prompt,
        custom_post_type_prompt=(custom_post_type.prompt_guidance if custom_post_type else ""),
    )

    if custom_post_type:
        BlogPostTitleSuggestion.objects.filter(id__in=[s.id for s in suggestions]).update(
            custom_post_type=custom_post_type
        )
        for suggestion in suggestions:
            suggestion.custom_post_type = custom_post_type

    # Render HTML for each suggestion using the Django template
    suggestions_html = []
    project_keywords = project.get_keywords()

    for suggestion in suggestions:
        # Add keyword usage info to each suggestion
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

        context = {
            "suggestion": suggestion,
            "has_pro_subscription": profile.is_on_pro_plan,
            "has_auto_submission_setting": project.has_auto_submission_setting,
        }
        html = render_to_string("components/blog_post_suggestion_card.html", context)
        suggestions_html.append(html)

    enqueue_track_event(
        profile_id=profile.id,
        event_name=ANALYTICS_EVENTS.TITLE_GENERATION_COMPLETED,
        properties={
            "project_id": project.id,
            "content_type": content_type,
            "title_count": len(suggestions),
            "result_status": "succeeded",
            "custom_post_type": custom_post_type.name if custom_post_type else "",
        },
        source_function="api.generate_title_suggestions",
    )

    return {
        "suggestions": suggestions,
        "suggestions_html": suggestions_html,
        "status": "success",
        "message": "",
    }


@api.post("/generate-title-from-idea", response=GenerateTitleSuggestionOut, auth=[session_auth])
def generate_title_from_idea(request: HttpRequest, data: GenerateTitleSuggestionsIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "title generation")
    if gate_error:
        return gate_error

    project = get_object_or_404(Project, id=data.project_id, profile=profile)

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.TITLE_GENERATION)
    if entitlement_error:
        return entitlement_error

    custom_post_type = get_custom_post_type_for_generation(
        project=project,
        post_type_id=data.post_type_id,
    )
    if data.post_type_id and custom_post_type is None:
        return {"status": "error", "message": "Custom post type not found for this project."}

    try:
        try:
            content_type = ContentType[data.content_type]
        except KeyError:
            return {"status": "error", "message": f"Invalid content type: {data.content_type}"}

        effective_user_prompt = build_effective_user_prompt(
            custom_post_type=custom_post_type,
            user_prompt=data.user_prompt,
        )

        suggestions = project.generate_title_suggestions(
            content_type=content_type,
            num_titles=1,
            user_prompt=effective_user_prompt,
            custom_post_type_prompt=(custom_post_type.prompt_guidance if custom_post_type else ""),
        )

        if not suggestions:
            return {"status": "error", "message": "No suggestions were generated"}

        suggestion = suggestions[0]

        if custom_post_type:
            suggestion.custom_post_type = custom_post_type
            suggestion.save(update_fields=["custom_post_type"])

        # Add keyword usage info to the suggestion
        project_keywords = project.get_keywords()
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

        # Render HTML for the suggestion using the Django template
        context = {
            "suggestion": suggestion,
            "has_pro_subscription": profile.is_on_pro_plan,
            "has_auto_submission_setting": project.has_auto_submission_setting,
        }
        suggestion_html = render_to_string("components/blog_post_suggestion_card.html", context)

        enqueue_track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.TITLE_GENERATION_COMPLETED,
            properties={
                "project_id": project.id,
                "content_type": content_type,
                "title_count": 1,
                "result_status": "succeeded",
                "custom_post_type": custom_post_type.name if custom_post_type else "",
            },
            source_function="api.generate_title_from_idea",
        )

        return {
            "status": "success",
            "suggestion": {
                "id": suggestion.id,
                "title": suggestion.title,
                "description": suggestion.description,
                "category": suggestion.category,
                "target_keywords": suggestion.target_keywords,
                "suggested_meta_description": suggestion.suggested_meta_description,
                "content_type": suggestion.content_type,
            },
            "suggestion_html": suggestion_html,
        }

    except Exception as e:
        logger.error(
            "Failed to generate title from idea",
            error=str(e),
            exc_info=True,
            project_id=project.id,
            user_prompt=data.user_prompt,
        )
        raise e


@api.post(
    "/generate-blog-content/{suggestion_id}", response=GeneratedContentOut, auth=[session_auth]
)
def generate_blog_content(request: HttpRequest, suggestion_id: int):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "blog content generation")
    if gate_error:
        return {
            "task_id": None,
            **gate_error,
        }

    suggestion = get_object_or_404(
        BlogPostTitleSuggestion, id=suggestion_id, project__profile=profile
    )

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.CONTENT_GENERATION)
    if entitlement_error:
        return {
            "task_id": None,
            **entitlement_error,
        }

    try:
        logger.info(
            "[Generate Blog Content] Queuing blog content generation task",
            suggestion_id=suggestion_id,
            profile_id=profile.id,
            project_id=suggestion.project.id,
        )

        task_id = async_task(
            "core.tasks.generate_blog_post_content",
            suggestion_id,
            group="Generate Blog Content",
        )

        logger.info(
            "[Generate Blog Content] Task queued successfully",
            suggestion_id=suggestion_id,
            task_id=task_id,
            profile_id=profile.id,
        )

        return {
            "status": "processing",
            "task_id": task_id,
            "message": "Content generation started. This may take 2-5 minutes.",
        }

    except ValueError as error:
        logger.error(
            "[Generate Blog Content] Validation error",
            error=str(error),
            exc_info=True,
            suggestion_id=suggestion_id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "task_id": None,
            "message": str(error),
        }
    except Exception as error:
        logger.error(
            "[Generate Blog Content] Unexpected error queuing task",
            error=str(error),
            exc_info=True,
            suggestion_id=suggestion_id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "task_id": None,
            "message": "An unexpected error occurred. Please try again later.",
        }


@api.get("/task-status/{task_id}", response=TaskStatusOut, auth=[session_auth])
def get_task_status(request: HttpRequest, task_id: str):
    """
    Check the status of an async task (e.g., blog content generation).

    Returns:
    - processing: Task is still running or not yet in database
    - completed: Task finished successfully, returns the generated blog post data
    - failed: Task failed with error message
    """

    profile = request.auth

    try:
        # Use result() to check task status
        # Returns None if task is still running or not yet in database
        task_result = result(task_id)

        if task_result is None:
            # Task is still processing or queued
            return {
                "status": "processing",
                "message": "Content generation in progress. Please check again in a few seconds.",
                "task_id": task_id,
            }

        # Task has completed, get the suggestion_id from the result
        # The task returns a string like "Successfully generated blog post X for Project Y"
        # We need to get the blog post from the database

        # Get the task from database to access args
        try:
            task = Task.objects.get(id=task_id)
            suggestion_id = task.args[0] if task.args else None

            if not suggestion_id:
                logger.error(
                    "[Task Status] No suggestion_id in task args",
                    task_id=task_id,
                    profile_id=profile.id,
                )
                return {
                    "status": "error",
                    "message": "Task completed but missing suggestion information",
                }

            suggestion = get_object_or_404(
                BlogPostTitleSuggestion,
                id=suggestion_id,
                project__profile=profile,
            )

            blog_post = suggestion.generated_blog_posts.first()

            if not blog_post:
                logger.error(
                    "[Task Status] Task succeeded but no blog post found",
                    task_id=task_id,
                    suggestion_id=suggestion_id,
                    profile_id=profile.id,
                )
                return {
                    "status": "error",
                    "message": "Content generation completed but blog post not found",
                }

            logger.info(
                "[Task Status] Task completed successfully",
                task_id=task_id,
                suggestion_id=suggestion_id,
                blog_post_id=blog_post.id,
                profile_id=profile.id,
            )

            return {
                "status": "completed",
                "message": "Content generated successfully",
                "blog_post_id": blog_post.id,
                "content": blog_post.content,
                "slug": blog_post.slug,
                "tags": blog_post.tags,
                "description": blog_post.description,
            }

        except Task.DoesNotExist:
            logger.warning(
                "[Task Status] Task not found in database",
                task_id=task_id,
                profile_id=profile.id,
            )
            return {
                "status": "processing",
                "message": "Content generation in progress. Please check again in a few seconds.",
            }

    except Exception as error:
        logger.error(
            "[Task Status] Unexpected error checking task status",
            task_id=task_id,
            error=str(error),
            exc_info=True,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "message": "Failed to check task status",
        }


@api.post("/projects/{project_id}/update", response={200: dict}, auth=[session_auth])
def update_project(request: HttpRequest, project_id: int):
    profile = request.auth
    logger.info("Updating project", project_id=project_id, profile_id=profile.id)
    project = get_object_or_404(Project, id=project_id, profile=profile)

    # Update project fields from form data
    project.key_features = request.POST.get("key_features", "")
    project.target_audience_summary = request.POST.get("target_audience_summary", "")
    project.pain_points = request.POST.get("pain_points", "")
    project.product_usage = request.POST.get("product_usage", "")
    project.links = request.POST.get("links", "")
    project.blog_theme = request.POST.get("blog_theme", "")
    project.founders = request.POST.get("founders", "")
    project.language = request.POST.get("language", "")
    project.summary = request.POST.get("summary", "")
    project.og_image_style = request.POST.get("og_image_style", "")

    project.save()

    return {"status": "success"}


@api.post(
    "/projects/{project_id}/toggle-auto-submission",
    response=ToggleAutoSubmissionOut,
    auth=[session_auth],
)
def toggle_auto_submission(request: HttpRequest, project_id: int):
    profile = request.auth
    project = get_object_or_404(Project, id=project_id, profile=profile)

    if not profile.is_on_pro_plan:
        return {
            "status": "error",
            "enabled": False,
            "message": "Automatic Post Submission is only available on the Pro plan. Please upgrade to access this feature.",  # noqa: E501
        }

    project.enable_automatic_post_submission = not project.enable_automatic_post_submission
    project.save(update_fields=["enable_automatic_post_submission"])

    return {"status": "success", "enabled": project.enable_automatic_post_submission}


@api.post(
    "/projects/{project_id}/toggle-og-image-generation",
    response=ToggleOGImageGenerationOut,
    auth=[session_auth],
)
def toggle_og_image_generation(request: HttpRequest, project_id: int):
    profile = request.auth
    project = get_object_or_404(Project, id=project_id, profile=profile)

    if not profile.is_on_pro_plan:
        return {
            "status": "error",
            "enabled": False,
            "message": "OG Image Generation is only available on the Pro plan. Please upgrade to access this feature.",  # noqa: E501
        }

    project.enable_automatic_og_image_generation = not project.enable_automatic_og_image_generation
    project.save(update_fields=["enable_automatic_og_image_generation"])

    return {"status": "success", "enabled": project.enable_automatic_og_image_generation}


@api.post(
    "/projects/{project_id}/toggle-link-exchange",
    response=ToggleLinkExchangeOut,
    auth=[session_auth],
)
def toggle_link_exchange(request: HttpRequest, project_id: int):
    profile = request.auth
    project = get_object_or_404(Project, id=project_id, profile=profile)

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.PRO_LINK_EXCHANGE)
    if entitlement_error:
        return {"enabled": False, **entitlement_error}

    project.particiate_in_link_exchange = not project.particiate_in_link_exchange
    project.save(update_fields=["particiate_in_link_exchange"])

    enqueue_track_event(
        profile_id=profile.id,
        event_name=ANALYTICS_EVENTS.LINK_EXCHANGE_TOGGLED,
        properties={
            "project_id": project.id,
            "enabled": project.particiate_in_link_exchange,
            "result_status": "succeeded",
        },
        source_function="api.toggle_link_exchange",
    )

    return {"status": "success", "enabled": project.particiate_in_link_exchange}


@api.post("/projects/update-sitemap-url", response=UpdateSitemapUrlOut, auth=[session_auth])
def update_sitemap_url(request: HttpRequest, data: UpdateSitemapUrlIn):
    """
    Update the sitemap URL for a project. When a sitemap URL is added or updated,
    it triggers automatic parsing and analysis of the sitemap pages.
    """
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "sitemap processing")
    if gate_error:
        return gate_error

    project = get_object_or_404(Project, id=data.project_id, profile=profile)

    sitemap_url = data.sitemap_url.strip()

    if not sitemap_url:
        return {
            "status": "error",
            "message": "Sitemap URL cannot be empty",
        }

    if not sitemap_url.startswith(("http://", "https://")):
        return {
            "status": "error",
            "message": "Sitemap URL must start with http:// or https://",
        }

    logger.info(
        "[Update Sitemap URL] Updating sitemap URL for project",
        project_id=project.id,
        profile_id=profile.id,
        sitemap_url=sitemap_url,
    )

    project.sitemap_url = sitemap_url
    project.save(update_fields=["sitemap_url"])

    # Trigger sitemap parsing task
    async_task("core.tasks.parse_sitemap_and_save_urls", project.id, group="Parse Sitemap")

    return {
        "status": "success",
        "message": "Sitemap URL updated successfully. Pages will be analyzed in batches of 10.",
    }


@api.post(
    "/project/{project_id}/sitemap/submit/", response=UpdateSitemapUrlOut, auth=[session_auth]
)
def submit_sitemap(request: HttpRequest, project_id: int, data: SubmitSitemapIn):
    """
    Submit/update the sitemap URL for a project. When a sitemap URL is added or updated,
    it triggers automatic parsing and analysis of the sitemap pages.
    """  # noqa: E501
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "sitemap processing")
    if gate_error:
        return gate_error

    project = get_object_or_404(Project, id=project_id, profile=profile)

    sitemap_url = data.sitemap_url.strip()

    if not sitemap_url:
        return {
            "status": "error",
            "message": "Sitemap URL cannot be empty",
        }

    if not sitemap_url.startswith(("http://", "https://")):
        return {
            "status": "error",
            "message": "Sitemap URL must start with http:// or https://",
        }

    logger.info(
        "[Submit Sitemap] Submitting sitemap URL for project",
        project_id=project.id,
        profile_id=profile.id,
        sitemap_url=sitemap_url,
    )

    project.sitemap_url = sitemap_url
    project.save(update_fields=["sitemap_url"])

    # Trigger sitemap parsing task
    async_task("core.tasks.parse_sitemap_and_save_urls", project.id, group="Parse Sitemap")

    return {
        "status": "success",
        "message": "Sitemap submitted successfully! Your pages will be analyzed shortly.",
    }


@api.post("/update-title-score/{suggestion_id}", response={200: dict}, auth=[session_auth])
def update_title_score(request: HttpRequest, suggestion_id: int, data: UpdateTitleScoreIn):
    profile = request.auth
    suggestion = get_object_or_404(
        BlogPostTitleSuggestion, id=suggestion_id, project__profile=profile
    )

    if data.score not in [-1, 0, 1]:
        return {"status": "error", "message": "Invalid score value. Must be -1, 0, or 1"}

    try:
        suggestion.user_score = data.score
        suggestion.save(update_fields=["user_score"])

        return {"status": "success", "message": "Score updated successfully"}

    except Exception as e:
        logger.error(
            "Failed to update title score",
            error=str(e),
            exc_info=True,
            suggestion_id=suggestion_id,
            profile_id=profile.id,
        )
        return {"status": "error", "message": f"Failed to update score: {str(e)}"}


@api.post("/suggestions/{suggestion_id}/archive-status", response={200: dict}, auth=[session_auth])
def update_archive_status(request: HttpRequest, suggestion_id: int, data: UpdateArchiveStatusIn):
    profile = request.auth
    suggestion = get_object_or_404(
        BlogPostTitleSuggestion, id=suggestion_id, project__profile=profile
    )

    try:
        suggestion.archived = data.archived
        suggestion.save(update_fields=["archived"])
        return {"status": "success"}
    except Exception as e:
        logger.error(
            "Failed to update suggestion archive status",
            error=str(e),
            exc_info=True,
            suggestion_id=suggestion_id,
            profile_id=profile.id,
        )
        return {"status": "error", "message": str(e)}


@api.post("/add-pricing-page", auth=[session_auth])
def add_pricing_page(request: HttpRequest, data: AddPricingPageIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "pricing page analysis")
    if gate_error:
        return gate_error

    project = Project.objects.filter(id=data.project_id, profile=profile).first()
    if project is None:
        return ownership_not_found_payload("Project")

    project_page = ProjectPage.objects.create(
        project=project, url=data.url, type=ProjectPageType.PRICING
    )

    project_page.get_page_content()
    project_page.analyze_content()

    return {"status": "success", "message": "Pricing page added successfully"}


@api.post("/add-competitor", response=CompetitorAnalysisOut, auth=[session_auth])
def add_competitor(request: HttpRequest, data: AddCompetitorIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "competitor analysis")
    if gate_error:
        return gate_error

    project = get_object_or_404(Project, id=data.project_id, profile=profile)

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.COMPETITOR_ADD)
    if entitlement_error:
        return entitlement_error

    try:
        if Competitor.objects.filter(project=project, url=data.url).exists():
            return {"status": "error", "message": "This competitor already exists for your project"}

        competitor = Competitor.objects.create(
            project=project,
            name=data.name if hasattr(data, "name") and data.name else "Processing...",
            url=data.url,
            description=data.description
            if hasattr(data, "description") and data.description
            else "",
        )

        # Fetch homepage content and populate name/description synchronously
        got_content = competitor.get_page_content()
        if got_content:
            competitor.populate_name_description()
            logger.info(
                "[Add Competitor] Successfully populated competitor details",
                competitor_id=competitor.id,
                competitor_name=competitor.name,
                project_id=project.id,
            )
        else:
            logger.warning(
                "[Add Competitor] Failed to get page content for competitor",
                competitor_id=competitor.id,
                url=data.url,
                project_id=project.id,
            )
            return {
                "status": "error",
                "message": "Failed to get page content for competitor",
            }

        return {
            "status": "success",
            "competitor_id": competitor.id,
            "name": competitor.name,
            "url": competitor.url,
            "message": "Competitor added successfully!",
        }

    except Exception as e:
        logger.error(
            "Failed to add competitor",
            error=str(e),
            exc_info=True,
            project_id=project.id,
            url=data.url,
        )
        return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}


@api.post(
    "/generate-competitor-vs-title", response=GenerateCompetitorVsTitleOut, auth=[session_auth]
)
def generate_competitor_vs_title(request: HttpRequest, data: GenerateCompetitorVsTitleIn):
    """Queue competitor comparison blog post generation and persist async status."""
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "competitor comparison generation")
    if gate_error:
        return gate_error

    competitor = Competitor.objects.select_related("project").filter(
        id=data.competitor_id,
        project__profile=profile,
    ).first()
    if competitor is None:
        return ownership_not_found_payload("Competitor")

    project = competitor.project

    if competitor.blog_post:
        return {
            "status": "success",
            "message": "VS blog post already generated.",
            "competitor_id": competitor.id,
        }

    entitlement_error = get_entitlement_error(
        profile,
        PlanEntitlement.COMPETITOR_POST_GENERATION,
    )
    if entitlement_error:
        return entitlement_error

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.CONTENT_GENERATION)
    if entitlement_error:
        return entitlement_error

    try:
        with transaction.atomic():
            competitor = (
                Competitor.objects.select_for_update()
                .select_related("project")
                .get(id=data.competitor_id, project__profile=profile)
            )

            if competitor.blog_post_generation_status == CompetitorPostGenerationStatus.PROCESSING:
                return {
                    "status": "processing",
                    "message": "A competitor post is already being generated. Please wait a few minutes and refresh.",
                    "competitor_id": competitor.id,
                }

            if competitor.blog_post:
                return {
                    "status": "success",
                    "message": "VS blog post already generated.",
                    "competitor_id": competitor.id,
                }

            competitor.blog_post_generation_status = CompetitorPostGenerationStatus.PROCESSING
            competitor.blog_post_generation_started_at = timezone.now()
            competitor.blog_post_generation_completed_at = None
            competitor.blog_post_generation_error = ""
            competitor.save(
                update_fields=[
                    "blog_post_generation_status",
                    "blog_post_generation_started_at",
                    "blog_post_generation_completed_at",
                    "blog_post_generation_error",
                ]
            )

        async_task(
            "core.tasks.generate_competitor_vs_blog_post",
            competitor.id,
            group="Generate Competitor VS Blog Post",
        )

        logger.info(
            "Queued VS competitor blog post generation",
            competitor_id=competitor.id,
            competitor_name=competitor.name,
            project_id=project.id,
            profile_id=profile.id,
        )

        return {
            "status": "processing",
            "message": "Generation started. This can take a few minutes. You can keep this page open or refresh later to view the post.",
            "competitor_id": competitor.id,
        }

    except Exception as e:
        logger.error(
            "Failed to queue competitor vs. blog post generation",
            error=str(e),
            exc_info=True,
            competitor_id=data.competitor_id,
            project_id=project.id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "message": "Failed to start competitor comparison blog post generation. Please try again.",
            "competitor_id": competitor.id,
        }


@api.get(
    "/competitor-post-generation-status/{competitor_id}",
    response=CompetitorPostGenerationStatusOut,
    auth=[session_auth],
)
def competitor_post_generation_status(request: HttpRequest, competitor_id: int):
    profile = request.auth

    competitor = get_object_or_404(
        Competitor.objects.select_related("project"),
        id=competitor_id,
        project__profile=profile,
    )

    if competitor.blog_post:
        if competitor.blog_post_generation_status != CompetitorPostGenerationStatus.COMPLETED:
            competitor.blog_post_generation_status = CompetitorPostGenerationStatus.COMPLETED
            competitor.blog_post_generation_completed_at = competitor.blog_post_generation_completed_at or timezone.now()
            competitor.blog_post_generation_error = ""
            competitor.save(
                update_fields=[
                    "blog_post_generation_status",
                    "blog_post_generation_completed_at",
                    "blog_post_generation_error",
                ]
            )

        return {
            "status": "completed",
            "message": "VS blog post is ready.",
            "competitor_id": competitor.id,
            "view_post_url": reverse(
                "competitor_blog_post_detail",
                kwargs={"project_pk": competitor.project_id, "pk": competitor.id},
            ),
        }

    if competitor.blog_post_generation_status == CompetitorPostGenerationStatus.PROCESSING:
        return {
            "status": "processing",
            "message": "Competitor comparison generation is still in progress.",
            "competitor_id": competitor.id,
            "view_post_url": None,
        }

    if competitor.blog_post_generation_status == CompetitorPostGenerationStatus.FAILED:
        return {
            "status": "failed",
            "message": competitor.blog_post_generation_error
            or "Competitor comparison generation failed. Please try again.",
            "competitor_id": competitor.id,
            "view_post_url": None,
        }

    return {
        "status": "idle",
        "message": "Ready to generate.",
        "competitor_id": competitor.id,
        "view_post_url": None,
    }


@api.post("/submit-feedback", auth=[session_auth])
def submit_feedback(request: HttpRequest, data: SubmitFeedbackIn):
    profile = request.auth
    Feedback.objects.create(profile=profile, feedback=data.feedback, page=data.page)
    return {"status": "success"}


@api.get("/user/settings", response=UserSettingsOut, auth=[session_auth])
def user_settings(request: HttpRequest, project_id: int):
    profile = request.auth
    try:
        project = get_object_or_404(Project, id=project_id, profile=profile)

        profile_data = {
            "has_pro_subscription": profile.is_on_pro_plan,
            "reached_content_generation_limit": profile.reached_content_generation_limit,
            "reached_title_generation_limit": profile.reached_title_generation_limit,
        }
        project_data = {
            "name": project.name,
            "url": project.url,
            "sitemap_url": project.sitemap_url,
            "has_auto_submission_setting": project.has_auto_submission_setting,
        }
        data = {"profile": profile_data, "project": project_data}

        return data
    except Exception as e:
        logger.error(
            "Error fetching user settings",
            error=str(e),
            project_id=project_id,
            profile_id=profile.id,
            exc_info=True,
        )
        raise


@api.post("/keywords/add", response=AddKeywordOut, auth=[session_auth])
def add_keyword_to_project(request: HttpRequest, data: AddKeywordIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "keyword enrichment")
    if gate_error:
        return gate_error

    project = get_object_or_404(Project, id=data.project_id, profile=profile)

    entitlement_error = get_entitlement_error(profile, PlanEntitlement.KEYWORD_ADD)
    if entitlement_error:
        return entitlement_error

    keyword_text_cleaned = data.keyword_text.strip().lower()
    if not keyword_text_cleaned:
        return {"status": "error", "message": "Keyword text cannot be empty."}

    try:
        keyword, created = Keyword.objects.get_or_create(
            keyword_text=keyword_text_cleaned,
        )

        project_keyword, pk_created = ProjectKeyword.objects.get_or_create(
            project=project, keyword=keyword
        )

        if created:
            metrics_fetched = keyword.fetch_and_update_metrics()
            if not metrics_fetched:
                logger.warning(
                    "[AddKeyword] Failed to fetch metrics for keyword.",
                    keyword_id=keyword.id,
                    project_id=project.id,
                )
                return {
                    "status": "error",
                    "message": "Keyword added, but metrics fetch failed/pending.",
                }

        keyword_data = {
            "id": keyword.id,
            "keyword_text": keyword.keyword_text,
            "volume": keyword.volume,
            "cpc_currency": keyword.cpc_currency,
            "cpc_value": float(keyword.cpc_value) if keyword.cpc_value is not None else None,
            "competition": keyword.competition,
            "country": keyword.country,
            "data_source": keyword.data_source,
            "last_fetched_at": keyword.last_fetched_at.isoformat()
            if keyword.last_fetched_at
            else None,
            "trend_data": [
                {"value": trend.value, "month": trend.month, "year": trend.year}
                for trend in keyword.trends.all()
            ],
        }

        enqueue_track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.KEYWORD_UPDATED,
            properties={
                "project_id": project.id,
                "keyword_id": keyword.id,
                "update_action": "added",
                "result_status": "succeeded",
                "already_existed": not created,
                "already_associated": not pk_created,
            },
            source_function="api.add_keyword_to_project",
        )

        return {
            "status": "success",
            "message": "Keyword added",
            "keyword": keyword_data,
        }

    except Exception as e:
        logger.error(
            "[AddKeyword] Failed to add keyword to project",
            error=str(e),
            exc_info=True,
            project_id=project.id,
            keyword_text=data.keyword_text,
        )
        return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}


@api.post("/keywords/toggle-use", response=ToggleProjectKeywordUseOut, auth=[session_auth])
def toggle_project_keyword_use(request: HttpRequest, data: ToggleProjectKeywordUseIn):
    profile = request.auth
    try:
        project = get_object_or_404(Project, id=data.project_id, profile=profile)
        project_keyword = get_object_or_404(
            ProjectKeyword, project=project, keyword_id=data.keyword_id
        )
        project_keyword.use = not project_keyword.use
        project_keyword.save(update_fields=["use"])

        enqueue_track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.KEYWORD_UPDATED,
            properties={
                "project_id": project.id,
                "keyword_id": project_keyword.keyword_id,
                "update_action": "toggled_use",
                "result_status": "succeeded",
                "use": project_keyword.use,
            },
            source_function="api.toggle_project_keyword_use",
        )

        return ToggleProjectKeywordUseOut(status="success", use=project_keyword.use)
    except Exception as e:
        logger.error(
            "Failed to toggle ProjectKeyword use field",
            error=str(e),
            exc_info=True,
            project_id=data.project_id,
            keyword_id=data.keyword_id,
            profile_id=profile.id,
        )
        return ToggleProjectKeywordUseOut(status="error", message=f"Failed to toggle use: {str(e)}")


@api.post("/keywords/delete", response=DeleteProjectKeywordOut, auth=[session_auth])
def delete_project_keyword(request: HttpRequest, data: DeleteProjectKeywordIn):
    profile = request.auth
    try:
        project = get_object_or_404(Project, id=data.project_id, profile=profile)
        project_keyword = get_object_or_404(
            ProjectKeyword, project=project, keyword_id=data.keyword_id
        )
        keyword_text = project_keyword.keyword.keyword_text
        keyword_id = project_keyword.keyword_id
        project_keyword.delete()

        enqueue_track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.KEYWORD_UPDATED,
            properties={
                "project_id": project.id,
                "keyword_id": keyword_id,
                "update_action": "deleted",
                "result_status": "succeeded",
            },
            source_function="api.delete_project_keyword",
        )

        return DeleteProjectKeywordOut(
            status="success", message=f"Keyword '{keyword_text}' removed from project"
        )
    except Exception as e:
        logger.error(
            "Failed to delete ProjectKeyword",
            error=str(e),
            exc_info=True,
            project_id=data.project_id,
            keyword_id=data.keyword_id,
            profile_id=profile.id,
        )
        return DeleteProjectKeywordOut(
            status="error", message=f"Failed to delete keyword: {str(e)}"
        )


@api.get("/keywords/details", response=GetKeywordDetailsOut, auth=[session_auth])
def get_keyword_details(request: HttpRequest, keyword_text: str, project_id: int):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "keyword enrichment")
    if gate_error:
        return GetKeywordDetailsOut(status="error", message=gate_error["message"])

    try:
        # Verify user has access to the project
        project = get_object_or_404(Project, id=project_id, profile=profile)

        # Clean the keyword text
        keyword_text_cleaned = keyword_text.strip().lower()
        if not keyword_text_cleaned:
            return GetKeywordDetailsOut(status="error", message="Keyword text cannot be empty")

        # Try to find existing keyword (pick most recent if multiple exist)
        keyword = (
            Keyword.objects.filter(keyword_text=keyword_text_cleaned)
            .order_by("-last_fetched_at")
            .first()
        )

        if keyword is None:
            # Create new keyword if it doesn't exist
            keyword = Keyword.objects.create(keyword_text=keyword_text_cleaned)
            # Try to fetch metrics for the new keyword
            metrics_fetched = keyword.fetch_and_update_metrics()
            if not metrics_fetched:
                logger.warning(
                    "[GetKeywordDetails] Failed to fetch metrics for new keyword",
                    keyword_id=keyword.id,
                    keyword_text=keyword_text_cleaned,
                    project_id=project_id,
                )

        # Check if keyword is already in the project and if it's in use
        project_keyword = ProjectKeyword.objects.filter(project=project, keyword=keyword).first()

        is_in_project = project_keyword is not None
        in_use = project_keyword.use if project_keyword else False

        # Prepare keyword data
        keyword_data = {
            "id": keyword.id,
            "keyword_text": keyword.keyword_text,
            "volume": keyword.volume,
            "cpc_currency": keyword.cpc_currency,
            "cpc_value": float(keyword.cpc_value) if keyword.cpc_value is not None else None,
            "competition": keyword.competition,
            "country": keyword.country,
            "is_in_project": is_in_project,
            "in_use": in_use,
            "project_keyword_id": project_keyword.id if project_keyword else None,
            "trend_data": [
                {"value": trend.value, "month": trend.month, "year": trend.year}
                for trend in keyword.trends.all()
            ],
        }

        return GetKeywordDetailsOut(status="success", keyword=keyword_data)

    except Exception as e:
        logger.error(
            "[GetKeywordDetails] Failed to get keyword details",
            error=str(e),
            exc_info=True,
            keyword_text=keyword_text,
            project_id=project_id,
            profile_id=profile.id,
        )
        return GetKeywordDetailsOut(
            status="error", message=f"Failed to get keyword details: {str(e)}"
        )


def _serialize_internal_blog_post(blog_post: BlogPost) -> dict:
    return {
        "id": blog_post.id,
        "title": blog_post.title,
        "description": blog_post.description,
        "slug": blog_post.slug,
        "tags": blog_post.tags,
        "content": blog_post.content,
        "status": blog_post.status,
        "icon_url": blog_post.icon.url if blog_post.icon else "",
        "image_url": blog_post.image.url if blog_post.image else "",
        "created_at": blog_post.created_at.isoformat(),
        "updated_at": blog_post.updated_at.isoformat(),
    }


def _attach_blog_post_media_from_urls(
    blog_post: BlogPost,
    *,
    icon_url: str | None,
    image_url: str | None,
    log_prefix: str,
):
    if icon_url:
        icon_url = icon_url.strip()
        if icon_url.startswith(("http://", "https://")):
            icon_content = download_image_from_url(
                image_url=icon_url,
                field_name="icon",
                instance_id=blog_post.id,
            )
            if icon_content:
                filename = f"blog-post-icon-{blog_post.id}.png"
                blog_post.icon.save(filename, icon_content, save=True)
                logger.info(
                    f"[{log_prefix}] Icon downloaded and saved",
                    blog_post_id=blog_post.id,
                    icon_url=icon_url,
                )

    if image_url:
        image_url = image_url.strip()
        if image_url.startswith(("http://", "https://")):
            image_content = download_image_from_url(
                image_url=image_url,
                field_name="image",
                instance_id=blog_post.id,
            )
            if image_content:
                filename = f"blog-post-image-{blog_post.id}.png"
                blog_post.image.save(filename, image_content, save=True)
                logger.info(
                    f"[{log_prefix}] Image downloaded and saved",
                    blog_post_id=blog_post.id,
                    image_url=image_url,
                )


@api.get("/internal/blog-posts", response=InternalBlogPostListOut, auth=[superuser_api_auth])
def list_internal_blog_posts(request: HttpRequest):
    blog_posts = BlogPost.objects.order_by("-created_at")
    return {
        "status": "success",
        "blog_posts": [_serialize_internal_blog_post(blog_post) for blog_post in blog_posts],
    }


@api.get(
    "/internal/blog-posts/{blog_post_id}",
    response={200: InternalBlogPostDetailOut, 404: BlogPostOut},
    auth=[superuser_api_auth],
)
def get_internal_blog_post(request: HttpRequest, blog_post_id: int):
    blog_post = BlogPost.objects.filter(id=blog_post_id).first()
    if not blog_post:
        return 404, {"status": "error", "message": "Blog post not found."}

    return {
        "status": "success",
        "blog_post": _serialize_internal_blog_post(blog_post),
    }


@api.post("/blog-posts/submit", response=BlogPostOut, auth=[superuser_api_auth])
def submit_blog_post(request: HttpRequest, data: BlogPostIn):
    try:
        blog_post = BlogPost.objects.create(
            title=data.title,
            description=data.description,
            slug=data.slug,
            tags=data.tags,
            content=data.content,
            status=data.status,
        )

        _attach_blog_post_media_from_urls(
            blog_post,
            icon_url=data.icon,
            image_url=data.image,
            log_prefix="Submit Blog Post",
        )

        return BlogPostOut(status="success", message="Blog post submitted successfully.")
    except Exception as e:
        logger.error(
            "[Submit Blog Post] Failed to submit blog post",
            error=str(e),
            exc_info=True,
            title=data.title,
            slug=data.slug,
        )
        return BlogPostOut(status="error", message="Failed to submit blog post")


@api.put(
    "/internal/blog-posts/{blog_post_id}",
    response={200: InternalBlogPostDetailOut, 404: BlogPostOut},
    auth=[superuser_api_auth],
)
def update_internal_blog_post(request: HttpRequest, blog_post_id: int, data: BlogPostIn):
    blog_post = BlogPost.objects.filter(id=blog_post_id).first()
    if not blog_post:
        return 404, {"status": "error", "message": "Blog post not found."}

    blog_post.title = data.title
    blog_post.description = data.description
    blog_post.slug = data.slug
    blog_post.tags = data.tags
    blog_post.content = data.content
    blog_post.status = data.status
    blog_post.save(update_fields=["title", "description", "slug", "tags", "content", "status"])

    _attach_blog_post_media_from_urls(
        blog_post,
        icon_url=data.icon,
        image_url=data.image,
        log_prefix="Update Blog Post",
    )

    return {
        "status": "success",
        "blog_post": _serialize_internal_blog_post(blog_post),
        "message": "Blog post updated successfully.",
    }


@api.patch(
    "/internal/blog-posts/{blog_post_id}",
    response={200: InternalBlogPostDetailOut, 400: BlogPostOut, 404: BlogPostOut},
    auth=[superuser_api_auth],
)
def patch_internal_blog_post(request: HttpRequest, blog_post_id: int, data: BlogPostUpdateIn):
    blog_post = BlogPost.objects.filter(id=blog_post_id).first()
    if not blog_post:
        return 404, {"status": "error", "message": "Blog post not found."}

    fields_to_update: list[str] = []

    if "title" in data.model_fields_set and data.title is not None:
        blog_post.title = data.title
        fields_to_update.append("title")
    if "description" in data.model_fields_set and data.description is not None:
        blog_post.description = data.description
        fields_to_update.append("description")
    if "slug" in data.model_fields_set and data.slug is not None:
        blog_post.slug = data.slug
        fields_to_update.append("slug")
    if "tags" in data.model_fields_set and data.tags is not None:
        blog_post.tags = data.tags
        fields_to_update.append("tags")
    if "content" in data.model_fields_set and data.content is not None:
        blog_post.content = data.content
        fields_to_update.append("content")
    if "status" in data.model_fields_set and data.status is not None:
        blog_post.status = data.status
        fields_to_update.append("status")

    if fields_to_update:
        blog_post.save(update_fields=fields_to_update)

    if "icon" in data.model_fields_set or "image" in data.model_fields_set:
        _attach_blog_post_media_from_urls(
            blog_post,
            icon_url=data.icon,
            image_url=data.image,
            log_prefix="Patch Blog Post",
        )

    if not fields_to_update and not ({"icon", "image"} & data.model_fields_set):
        return 400, {"status": "error", "message": "No fields provided for update."}

    return {
        "status": "success",
        "blog_post": _serialize_internal_blog_post(blog_post),
        "message": "Blog post updated successfully.",
    }


@api.delete(
    "/internal/blog-posts/{blog_post_id}",
    response={200: BlogPostOut, 404: BlogPostOut},
    auth=[superuser_api_auth],
)
def delete_internal_blog_post(request: HttpRequest, blog_post_id: int):
    blog_post = BlogPost.objects.filter(id=blog_post_id).first()
    if not blog_post:
        return 404, {"status": "error", "message": "Blog post not found."}

    blog_post.delete()
    return {"status": "success", "message": "Blog post deleted successfully."}


@api.post("/post-generated-blog-post", response=PostGeneratedBlogPostOut, auth=[session_auth])
def post_generated_blog_post(request: HttpRequest, data: PostGeneratedBlogPostIn):
    profile = request.auth

    blog_post_id = data.id
    if not blog_post_id:
        return {"status": "error", "message": "Missing generated blog post id."}
    try:
        generated_post = GeneratedBlogPost.objects.filter(
            id=blog_post_id,
            project__profile=profile,
        ).first()
        if generated_post is None:
            return ownership_not_found_payload("Generated blog post")

        enqueue_track_event(
            profile_id=profile.id,
            event_name=ANALYTICS_EVENTS.PUBLISH_ATTEMPTED,
            properties={
                "project_id": getattr(generated_post, "project_id", None),
                "blog_post_id": generated_post.id,
                "result_status": "succeeded",
            },
            source_function="api.post_generated_blog_post",
        )

        if generated_post.publish_approval_status != GeneratedBlogPost.ApprovalStatus.APPROVED:
            generated_post.create_workflow_audit_event(
                checkpoint="PUBLISH",
                event_type="ACTION_BLOCKED",
                actor_profile=profile,
                decision=generated_post.publish_approval_status,
                reason="awaiting_publish_approval",
            )
            enqueue_track_event(
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PUBLISH_FAILED,
                properties={
                    "project_id": getattr(generated_post, "project_id", None),
                    "blog_post_id": generated_post.id,
                    "result_status": "failed",
                    "failure_reason": "awaiting_publish_approval",
                },
                source_function="api.post_generated_blog_post",
            )
            return {
                "status": "error",
                "message": (
                    "Publish blocked by approval checkpoint: "
                    f"current_status={generated_post.publish_approval_status}"
                ),
            }

        quality_gate_result = evaluate_pre_publish_quality_gate(generated_post)
        if quality_gate_result["decision"] == "block":
            logger.warning(
                "[Publish Quality Gate] Blocking publish attempt",
                blog_post_id=blog_post_id,
                profile_id=profile.id,
                checks=quality_gate_result["blocking_checks"],
            )
            generated_post.create_workflow_audit_event(
                checkpoint="PUBLISH",
                event_type="QUALITY_GATE_BLOCKED",
                actor_profile=profile,
                decision="BLOCKED",
                reason=quality_gate_result["summary"],
                metadata={"blocking_checks": quality_gate_result["blocking_checks"]},
            )
            enqueue_track_event(
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PUBLISH_FAILED,
                properties={
                    "project_id": getattr(generated_post, "project_id", None),
                    "blog_post_id": generated_post.id,
                    "result_status": "failed",
                    "failure_reason": "quality_gate_blocked",
                },
                source_function="api.post_generated_blog_post",
            )
            return {
                "status": "error",
                "message": f"Publish blocked by quality gate: {quality_gate_result['summary']}",
            }

        if quality_gate_result["decision"] == "warn":
            logger.warning(
                "[Publish Quality Gate] Publish allowed with warnings",
                blog_post_id=blog_post_id,
                profile_id=profile.id,
                checks=quality_gate_result["warning_checks"],
                aggregate_score=quality_gate_result["aggregate_score"],
            )

        result = generated_post.submit_blog_post_to_endpoint()
        if result is True:
            generated_post.posted = True
            generated_post.date_posted = timezone.now()
            generated_post.save(update_fields=["posted", "date_posted"])

            if quality_gate_result["decision"] == "warn":
                publish_message = (
                    f"Blog post published with quality warnings: {quality_gate_result['summary']}"
                )
            else:
                publish_message = "Blog post published!"

            generated_post.create_workflow_audit_event(
                checkpoint="PUBLISH",
                event_type="PUBLISHED",
                actor_profile=profile,
                decision="SUCCESS",
                reason=publish_message,
                metadata={"quality_gate_decision": quality_gate_result["decision"]},
            )
            enqueue_track_event(
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PUBLISH_SUCCEEDED,
                properties={
                    "project_id": getattr(generated_post, "project_id", None),
                    "blog_post_id": generated_post.id,
                    "result_status": "succeeded",
                },
                source_function="api.post_generated_blog_post",
            )
            return {"status": "success", "message": publish_message}
        else:
            generated_post.create_workflow_audit_event(
                checkpoint="PUBLISH",
                event_type="PUBLISH_FAILED",
                actor_profile=profile,
                decision="FAILED",
                reason="endpoint_submission_failed",
            )
            enqueue_track_event(
                profile_id=profile.id,
                event_name=ANALYTICS_EVENTS.PUBLISH_FAILED,
                properties={
                    "project_id": getattr(generated_post, "project_id", None),
                    "blog_post_id": generated_post.id,
                    "result_status": "failed",
                    "failure_reason": "endpoint_submission_failed",
                },
                source_function="api.post_generated_blog_post",
            )
            return {"status": "error", "message": "Failed to post blog."}
    except Exception as e:
        logger.error(
            "Failed to post generated blog post",
            error=str(e),
            blog_post_id=blog_post_id,
            exc_info=True,
        )
        return {"status": "error", "message": str(e)}


@api.post("/fix-generated-blog-post", response=FixGeneratedBlogPostOut, auth=[session_auth])
def fix_generated_blog_post(request: HttpRequest, data: FixGeneratedBlogPostIn):
    profile = request.auth

    blog_post_id = data.id
    if not blog_post_id:
        return {"status": "error", "message": "Missing generated blog post id."}

    try:
        generated_post = GeneratedBlogPost.objects.filter(
            id=blog_post_id,
            project__profile=profile,
        ).first()
        if generated_post is None:
            return ownership_not_found_payload("Generated blog post")

        return {"status": "success", "message": "Blog post issues have been fixed successfully."}

    except Exception as e:
        logger.error(
            "Failed to fix generated blog post",
            error=str(e),
            blog_post_id=blog_post_id,
            exc_info=True,
        )
        return {"status": "error", "message": f"Failed to fix blog post: {str(e)}"}


@api.post(
    "/project-pages/toggle-always-use", response=ToggleProjectPageAlwaysUseOut, auth=[session_auth]
)
def toggle_project_page_always_use(request: HttpRequest, data: ToggleProjectPageAlwaysUseIn):
    """
    Toggle the always_use field for a ProjectPage.
    When enabled, the page link will always be included in generated blog posts.
    """  # noqa: E501
    profile = request.auth

    try:
        project_page = get_object_or_404(ProjectPage, id=data.page_id, project__profile=profile)

        project_page.always_use = not project_page.always_use
        project_page.save(update_fields=["always_use"])

        return {
            "status": "success",
            "always_use": project_page.always_use,
        }

    except Exception as error:
        logger.error(
            "Failed to toggle ProjectPage always_use field",
            error=str(error),
            exc_info=True,
            page_id=data.page_id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "always_use": False,
            "message": f"Failed to toggle always use: {str(error)}",
        }


@api.post("/generate-og-image", response=GenerateOGImageOut, auth=[session_auth])
def generate_og_image(request: HttpRequest, data: GenerateOGImageIn):
    """
    Generate an Open Graph image for a blog post using Replicate flux-schnell model.
    """
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "OG image generation")
    if gate_error:
        return gate_error

    if not settings.REPLICATE_API_TOKEN:
        logger.error(
            "[GenerateOGImage] Replicate API token not configured",
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "message": "Image generation service is not configured. Please contact support.",
        }

    try:
        generated_post = get_object_or_404(
            GeneratedBlogPost,
            id=data.blog_post_id,
            project__profile=profile,
        )

        if generated_post.image:
            logger.info(
                "[GenerateOGImage] Image already exists for blog post",
                blog_post_id=generated_post.id,
                profile_id=profile.id,
            )
            return {
                "status": "success",
                "message": "Image already exists",
                "image_url": generated_post.image.url,
            }

        success, _ = generated_post.generate_og_image()

        if success:
            return {
                "status": "success",
                "message": "OG image generated successfully",
                "image_url": generated_post.image.url,
            }
        else:
            return {
                "status": "error",
                "message": "Failed to generate image. Please try again.",
            }

    except GeneratedBlogPost.DoesNotExist:
        return {
            "status": "error",
            "message": "Blog post not found",
        }
    except replicate.exceptions.ReplicateError as error:
        logger.error(
            "[GenerateOGImage] Replicate API error",
            error=str(error),
            exc_info=True,
            blog_post_id=data.blog_post_id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "message": "Failed to generate image. Please try again later.",
        }
    except Exception as error:
        logger.error(
            "[GenerateOGImage] Unexpected error generating OG image",
            error=str(error),
            exc_info=True,
            blog_post_id=data.blog_post_id,
            profile_id=profile.id,
        )
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(error)}",
        }
