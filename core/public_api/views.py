from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django_q.tasks import async_task
from ninja import NinjaAPI

from core.abuse_prevention import enforce_verified_email_for_expensive_action
from core.api_error_semantics import PlanEntitlement, error_payload, evaluate_plan_entitlement
from core.choices import ContentType, ExecutionJobOperation, ExecutionJobStatus
from core.models import (
    AgentExecutionJob,
    AutoSubmissionSetting,
    BlogPostTitleSuggestion,
    Competitor,
    GeneratedBlogPost,
    Keyword,
    Project,
    ProjectKeyword,
    ProjectPage,
)
from core.outcome_attribution import (
    get_project_outcome_attribution_report,
    get_project_reporting_snapshot,
)
from core.public_api.auth import public_api_key_auth
from core.publish_quality_gate import evaluate_pre_publish_quality_gate
from core.public_api.schemas import (
    PublicAPIErrorOut,
    PublicAccountOut,
    PublicBlogPostApprovalReviewIn,
    PublicBlogPostApprovalReviewOut,
    PublicBlogPostGenerateIn,
    PublicBlogPostGenerateOut,
    PublicBlogPostGetOut,
    PublicBlogPostListOut,
    PublicBlogPostPublishOut,
    PublicContentAutomationIn,
    PublicContentAutomationOut,
    PublicCompetitorCreateIn,
    PublicCompetitorCreateOut,
    PublicCompetitorGetOut,
    PublicCompetitorListOut,
    PublicExecutionJobActionOut,
    PublicExecutionJobCreateIn,
    PublicExecutionJobCreateOut,
    PublicExecutionJobGetOut,
    PublicExecutionJobListOut,
    PublicKeywordCreateIn,
    PublicKeywordCreateOut,
    PublicKeywordGetOut,
    PublicKeywordListOut,
    PublicOutcomeAttributionOut,
    PublicReportingSnapshotOut,
    PublicProjectCreateOut,
    PublicProjectGetOut,
    PublicProjectIn,
    PublicProjectListOut,
    PublicProjectPageCreateIn,
    PublicProjectPageCreateOut,
    PublicProjectPageGetOut,
    PublicProjectPageListOut,
    PublicProjectUpdateIn,
    PublicProjectUpdateOut,
    PublicTitleSuggestionCreateIn,
    PublicTitleSuggestionCreateOut,
    PublicTitleSuggestionGetOut,
    PublicTitleSuggestionListOut,
)
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


def get_public_pricing_url() -> str:
    base_url = settings.SITE_URL.rstrip("/") if getattr(settings, "SITE_URL", "") else ""
    return f"{base_url}{reverse('pricing')}" if base_url else reverse("pricing")


def plan_gate_error(code: str, message: str, *, status_code: int = 403) -> tuple[int, dict]:
    return (
        status_code,
        error_payload(code=code, message=message, upgrade_url=get_public_pricing_url()),
    )


def get_public_entitlement_error(profile, entitlement: PlanEntitlement) -> dict | None:
    return evaluate_plan_entitlement(
        profile,
        entitlement,
        upgrade_url=get_public_pricing_url(),
    )


public_api = NinjaAPI(
    title="TuxSEO Public API",
    version="1.0.0",
    urls_namespace="public_api",
    docs_url="/docs",
    openapi_url="/openapi.json",
)


def get_verified_email_gate_error(profile, action_name: str) -> dict | None:
    return enforce_verified_email_for_expensive_action(profile=profile, action_name=action_name)


def serialize_public_project(project: Project) -> dict:
    return {
        "project_id": project.id,
        "name": project.name,
        "type": project.get_type_display(),
        "url": project.url,
        "summary": project.summary,
        "blog_theme": project.blog_theme,
        "founders": project.founders,
        "key_features": project.key_features,
        "target_audience_summary": project.target_audience_summary,
        "pain_points": project.pain_points,
        "product_usage": project.product_usage,
        "links": project.links,
        "language": project.language,
        "location": project.location,
    }


def get_public_title_suggestion_status(suggestion: BlogPostTitleSuggestion) -> str:
    if suggestion.archived:
        return "archived"
    if suggestion.generated_blog_posts.filter(posted=True).exists():
        return "published"
    return "unpublished"


def serialize_public_title_suggestion(suggestion: BlogPostTitleSuggestion) -> dict:
    return {
        "id": suggestion.id,
        "title": suggestion.title,
        "category": suggestion.category,
        "description": suggestion.description,
        "target_keywords": suggestion.target_keywords or [],
        "suggested_meta_description": suggestion.suggested_meta_description,
        "content_type": suggestion.content_type,
        "status": get_public_title_suggestion_status(suggestion),
    }


def serialize_public_keyword(project_keyword: ProjectKeyword) -> dict:
    keyword = project_keyword.keyword
    return {
        "id": keyword.id,
        "keyword_text": keyword.keyword_text,
        "volume": keyword.volume,
        "cpc_currency": keyword.cpc_currency,
        "cpc_value": float(keyword.cpc_value) if keyword.cpc_value is not None else None,
        "competition": keyword.competition,
        "country": keyword.country,
        "data_source": keyword.data_source,
        "last_fetched_at": keyword.last_fetched_at.isoformat() if keyword.last_fetched_at else None,
        "trend_data": [
            {"value": trend.value, "month": trend.month, "year": trend.year}
            for trend in keyword.trends.all()
        ],
        "project_keyword_id": project_keyword.id,
        "in_use": project_keyword.use,
    }


def serialize_public_competitor(competitor: Competitor) -> dict:
    return {
        "id": competitor.id,
        "project_id": competitor.project_id,
        "name": competitor.name,
        "url": competitor.url,
        "description": competitor.description,
        "summary": competitor.summary or "",
        "homepage_title": competitor.homepage_title or "",
        "homepage_description": competitor.homepage_description or "",
        "date_scraped": competitor.date_scraped.isoformat() if competitor.date_scraped else None,
        "date_analyzed": competitor.date_analyzed.isoformat() if competitor.date_analyzed else None,
        "blog_post_generation_status": competitor.blog_post_generation_status,
        "blog_post_generation_started_at": (
            competitor.blog_post_generation_started_at.isoformat()
            if competitor.blog_post_generation_started_at
            else None
        ),
        "blog_post_generation_completed_at": (
            competitor.blog_post_generation_completed_at.isoformat()
            if competitor.blog_post_generation_completed_at
            else None
        ),
        "blog_post_generation_error": competitor.blog_post_generation_error or "",
        "created_at": competitor.created_at.isoformat(),
        "updated_at": competitor.updated_at.isoformat(),
    }


def serialize_public_project_page(project_page: ProjectPage) -> dict:
    return {
        "id": project_page.id,
        "project_id": project_page.project_id,
        "url": project_page.url,
        "source": project_page.source,
        "always_use": project_page.always_use,
        "type": project_page.type or "",
        "type_ai_guess": project_page.type_ai_guess or "",
        "title": project_page.title or "",
        "description": project_page.description or "",
        "summary": project_page.summary or "",
        "date_scraped": project_page.date_scraped.isoformat() if project_page.date_scraped else None,
        "date_analyzed": project_page.date_analyzed.isoformat() if project_page.date_analyzed else None,
        "created_at": project_page.created_at.isoformat(),
        "updated_at": project_page.updated_at.isoformat(),
    }


def serialize_public_blog_post(
    blog_post: GeneratedBlogPost,
    *,
    include_content: bool = True,
    include_link_audit: bool = False,
    include_workflow_audit: bool = False,
) -> dict:
    payload = {
        "id": blog_post.id,
        "title": blog_post.title,
        "slug": blog_post.slug,
        "description": blog_post.description,
        "tags": blog_post.tags,
        "posted": blog_post.posted,
        "date_posted": blog_post.date_posted.isoformat() if blog_post.date_posted else None,
        "title_suggestion_id": blog_post.title_suggestion_id,
        "content": blog_post.content if include_content else None,
        "publish_approval_status": blog_post.publish_approval_status,
        "external_links_approval_status": blog_post.external_links_approval_status,
        "publish_review_reason": blog_post.publish_review_reason or "",
        "external_links_review_reason": blog_post.external_links_review_reason or "",
        "link_audit_logs": [],
        "workflow_audit_logs": [],
    }

    if include_link_audit:
        logs = blog_post.link_opportunity_audit_logs.order_by("created_at")
        payload["link_audit_logs"] = [
            {
                "id": log.id,
                "phase": log.phase,
                "decision": log.decision,
                "candidate_url": log.candidate_url,
                "candidate_domain": log.candidate_domain,
                "link_source": log.link_source,
                "relevance_score": log.relevance_score,
                "relevance_threshold": log.relevance_threshold,
                "proposed_anchor": log.proposed_anchor,
                "final_anchor": log.final_anchor,
                "relation": log.relation,
                "policy_flags": log.policy_flags or [],
                "reasons": log.reasons or [],
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]

    if include_workflow_audit:
        payload["workflow_audit_logs"] = [
            {
                "id": log.id,
                "checkpoint": log.checkpoint,
                "event_type": log.event_type,
                "decision": log.decision,
                "reason": log.reason,
                "actor_profile_id": log.actor_profile_id,
                "metadata": log.metadata or {},
                "created_at": log.created_at.isoformat(),
            }
            for log in blog_post.workflow_audit_logs.order_by("created_at")
        ]

    return payload


def serialize_public_execution_job(job: AgentExecutionJob) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "operation": job.operation,
        "status": job.status.lower(),
        "idempotency_key": job.idempotency_key,
        "payload": job.payload or {},
        "result": job.result or {},
        "error_code": job.error_code or "",
        "error_message": job.error_message or "",
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "canceled_at": job.canceled_at.isoformat() if job.canceled_at else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def execution_error(code: str, message: str, *, status_code: int) -> tuple[int, dict]:
    return (
        status_code,
        {
            "status": "error",
            "code": code,
            "message": message,
        },
    )


def get_request_idempotency_key(request: HttpRequest) -> str:
    header_key = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("idempotency-key")
        or request.headers.get("X-Idempotency-Key")
        or request.headers.get("x-idempotency-key")
    )
    return (header_key or "").strip()


@public_api.get(
    "/account",
    response=PublicAccountOut,
    auth=[public_api_key_auth],
    tags=["Account"],
)
def get_public_account(request: HttpRequest):
    profile = request.auth

    return {
        "account_id": profile.id,
        "email": profile.user.email,
        "product_name": profile.product_name,
        "is_on_pro_plan": profile.is_on_pro_plan,
        "project_limit": profile.project_limit,
        "active_project_count": profile.number_of_active_projects,
    }


@public_api.get(
    "/projects",
    response=PublicProjectListOut,
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def list_public_projects(request: HttpRequest, page: int = 1, page_size: int = 20):
    profile = request.auth

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    projects_query = Project.objects.filter(profile=profile).order_by("-updated_at", "-created_at")
    total = projects_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    projects = list(projects_query[start_index:end_index])

    return {
        "status": "success",
        "projects": [serialize_public_project(project) for project in projects],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.post(
    "/projects",
    response={
        200: PublicProjectCreateOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        500: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def create_public_project(request: HttpRequest, data: PublicProjectIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "project creation")
    if gate_error:
        return 400, {"message": gate_error["message"]}

    project_url = data.url.strip()
    if not project_url:
        return 400, {"message": "Project URL cannot be empty"}

    if not project_url.startswith(("http://", "https://")):
        return 400, {"message": "Project URL must start with http:// or https://"}

    if Project.objects.filter(profile=profile, url=project_url).exists():
        return 400, {"message": "You already added this project URL"}

    if not profile.can_create_project:
        if profile.is_on_free_plan:
            limit = profile.project_limit
            limit_message = (
                f"Project creation limit reached ({limit} project on Free plan). "
                "Upgrade to Pro to create more projects."
            )
            return plan_gate_error(
                "FREE_PLAN_PROJECT_LIMIT_REACHED",
                limit_message,
            )

        return 400, {"message": "Project creation limit reached. Contact support for assistance."}

    project = profile.get_or_create_project(url=project_url, source=data.source)

    try:
        got_project_content = project.get_page_content()
        if not got_project_content:
            project.delete()
            return 400, {"message": "Failed to get page content"}

        is_project_analyzed = project.analyze_content()
        if not is_project_analyzed:
            project.delete()
            return 400, {"message": "Failed to analyze project"}

        try:
            async_task(
                "core.tasks.auto_discover_and_ingest_sitemap",
                project.id,
                group="Discover Sitemap",
            )
        except Exception as task_error:
            logger.warning(
                "[Public API] Failed to enqueue sitemap auto-discovery",
                project_id=project.id,
                profile_id=profile.id,
                error=str(task_error),
            )

        return {
            "status": "success",
            "project": serialize_public_project(project),
        }
    except Exception as error:
        logger.error(
            "[Public API] Unexpected error during project creation",
            error=str(error),
            exc_info=True,
            profile_id=profile.id,
            url=project_url,
        )
        if project.id:
            project.delete()
        return 500, {"message": "An unexpected error occurred while creating the project"}


@public_api.get(
    "/projects/{project_id}",
    response={200: PublicProjectGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def get_public_project(request: HttpRequest, project_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    return {"status": "success", "project": serialize_public_project(project)}


@public_api.get(
    "/projects/{project_id}/outcome-attribution",
    response={200: PublicOutcomeAttributionOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def get_public_project_outcome_attribution(request: HttpRequest, project_id: int, days: int = 30):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    normalized_days = min(max(days, 1), 365)
    window_end = timezone.now().date()
    window_start = window_end - timedelta(days=normalized_days - 1)

    report = get_project_outcome_attribution_report(
        project=project,
        start_date=window_start,
        end_date=window_end,
    )

    return {
        "status": "success",
        **report,
    }


@public_api.get(
    "/projects/{project_id}/reporting-snapshot",
    response={200: PublicReportingSnapshotOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def get_public_project_reporting_snapshot(request: HttpRequest, project_id: int, days: int = 30):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    normalized_days = min(max(days, 1), 365)
    window_end = timezone.now().date()
    window_start = window_end - timedelta(days=normalized_days - 1)

    snapshot = get_project_reporting_snapshot(
        project=project,
        start_date=window_start,
        end_date=window_end,
    )

    return {
        "status": "success",
        **snapshot,
    }


@public_api.patch(
    "/projects/{project_id}",
    response={200: PublicProjectUpdateOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Projects"],
)
def update_public_project(request: HttpRequest, project_id: int, data: PublicProjectUpdateIn):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    update_data = data.model_dump(exclude_none=True)
    if not update_data:
        return 400, {"message": "At least one field is required for update"}

    cleaned_update_data = {}
    for field_name, field_value in update_data.items():
        if isinstance(field_value, str):
            cleaned_update_data[field_name] = field_value.strip()
        else:
            cleaned_update_data[field_name] = field_value

    if "name" in cleaned_update_data and cleaned_update_data["name"] == "":
        return 400, {"message": "Project name cannot be empty"}

    for field_name, field_value in cleaned_update_data.items():
        setattr(project, field_name, field_value)
    project.save(update_fields=list(cleaned_update_data.keys()))

    return {"status": "success", "project": serialize_public_project(project)}


@public_api.post(
    "/projects/{project_id}/content-automation",
    response={
        200: PublicContentAutomationOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
        500: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Content Automation"],
    include_in_schema=False,
)
def configure_content_automation(
    request: HttpRequest, project_id: int, data: PublicContentAutomationIn
):
    profile = request.auth

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    if not profile.is_on_pro_plan:
        return plan_gate_error(
            "PRO_PLAN_REQUIRED_CONTENT_AUTOMATION",
            "Automatic Post Submission is only available on the Pro plan. Upgrade to Pro to configure automation.",
        )

    endpoint_url = data.endpoint_url.strip()
    if not endpoint_url:
        return 400, {"message": "Endpoint URL cannot be empty"}

    if not endpoint_url.startswith(("http://", "https://")):
        return 400, {"message": "Endpoint URL must start with http:// or https://"}

    try:
        content_automation, _ = AutoSubmissionSetting.objects.update_or_create(
            project=project,
            defaults={
                "endpoint_url": endpoint_url,
                "body": data.request_body_json,
                "header": data.request_headers_json,
                "posts_per_month": data.posts_per_month,
            },
        )

        project.enable_automatic_post_submission = data.enable_automatic_post_submission
        project.save(update_fields=["enable_automatic_post_submission"])

        return {
            "status": "success",
            "message": "Content automation settings saved",
            "project_id": project.id,
            "content_automation_id": content_automation.id,
            "enable_automatic_post_submission": project.enable_automatic_post_submission,
        }
    except Exception as error:
        logger.error(
            "[Public API] Failed to configure content automation",
            error=str(error),
            exc_info=True,
            project_id=project_id,
            profile_id=profile.id,
        )
        return 500, {"message": "Failed to save content automation settings"}


@public_api.get(
    "/projects/{project_id}/title-suggestions",
    response={200: PublicTitleSuggestionListOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Title Suggestions"],
)
def list_public_title_suggestions(
    request: HttpRequest,
    project_id: int,
    status: str = "all",
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    if status not in {"all", "unpublished", "published", "archived"}:
        return 400, {"message": "Invalid status filter"}

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    suggestions_query = BlogPostTitleSuggestion.objects.filter(project=project)
    if status == "archived":
        suggestions_query = suggestions_query.filter(archived=True)
    elif status == "published":
        suggestions_query = suggestions_query.filter(
            archived=False, generated_blog_posts__posted=True
        ).distinct()
    elif status == "unpublished":
        suggestions_query = suggestions_query.filter(archived=False).exclude(
            generated_blog_posts__posted=True
        )

    suggestions_query = suggestions_query.order_by("-created_at")
    total = suggestions_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    suggestions = list(suggestions_query[start_index:end_index])

    return {
        "status": "success",
        "suggestions": [serialize_public_title_suggestion(suggestion) for suggestion in suggestions],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.get(
    "/projects/{project_id}/title-suggestions/{suggestion_id}",
    response={200: PublicTitleSuggestionGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Title Suggestions"],
)
def get_public_title_suggestion(request: HttpRequest, project_id: int, suggestion_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    suggestion = BlogPostTitleSuggestion.objects.filter(id=suggestion_id, project=project).first()
    if suggestion is None:
        return 404, {"message": "Title suggestion not found"}

    return {"status": "success", "suggestion": serialize_public_title_suggestion(suggestion)}


@public_api.post(
    "/projects/{project_id}/title-suggestions",
    response={
        200: PublicTitleSuggestionCreateOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Title Suggestions"],
)
def create_public_title_suggestions(
    request: HttpRequest, project_id: int, data: PublicTitleSuggestionCreateIn
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    try:
        content_type = ContentType[data.content_type]
    except KeyError:
        return 400, {"message": f"Invalid content type: {data.content_type}"}

    if not getattr(profile, "can_generate_title_suggestions", True):
        limit = getattr(profile, "title_suggestion_limit", None)
        current_count = getattr(profile, "number_of_title_suggestions_this_month", None)
        usage_text = f" ({current_count}/{limit} this month)" if limit is not None and current_count is not None else ""
        return plan_gate_error(
            "FREE_PLAN_TITLE_SUGGESTION_LIMIT_REACHED",
            "Title suggestion generation limit reached on your current plan"
            f"{usage_text}. Upgrade to Pro for higher limits.",
        )

    suggestions = project.generate_title_suggestions(
        content_type=content_type,
        num_titles=data.count,
        user_prompt=data.seed_guidance.strip(),
    )
    serialized_suggestions = [
        serialize_public_title_suggestion(suggestion) for suggestion in suggestions
    ]

    return {
        "status": "success",
        "count": len(serialized_suggestions),
        "suggestions": serialized_suggestions,
    }


@public_api.get(
    "/projects/{project_id}/keywords",
    response={200: PublicKeywordListOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Keywords"],
)
def list_public_keywords(
    request: HttpRequest,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    keyword_query = ProjectKeyword.objects.filter(project=project).select_related("keyword")
    keyword_query = keyword_query.order_by("-date_associated")

    total = keyword_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    keywords = list(keyword_query[start_index:end_index])

    return {
        "status": "success",
        "keywords": [serialize_public_keyword(project_keyword) for project_keyword in keywords],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.get(
    "/projects/{project_id}/keywords/{keyword_id}",
    response={200: PublicKeywordGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Keywords"],
)
def get_public_keyword(request: HttpRequest, project_id: int, keyword_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    project_keyword = ProjectKeyword.objects.select_related("keyword").filter(
        project=project, keyword_id=keyword_id
    ).first()
    if project_keyword is None:
        return 404, {"message": "Keyword not found"}

    return {"status": "success", "keyword": serialize_public_keyword(project_keyword)}


@public_api.post(
    "/projects/{project_id}/keywords",
    response={
        200: PublicKeywordCreateOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Keywords"],
)
def create_public_keyword(request: HttpRequest, project_id: int, data: PublicKeywordCreateIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "keyword enrichment")
    if gate_error:
        return 400, {"message": gate_error["message"]}

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    if not profile.can_add_keywords:
        if profile.is_on_free_plan:
            message = (
                "Keyword additions are not available on the Free plan. "
                "Upgrade to Pro to add custom keywords."
            )
            return plan_gate_error("PRO_PLAN_REQUIRED_KEYWORD_ADDITION", message)

        message = "Keyword limit reached. Contact support for assistance."
        return 400, {"message": message}

    keyword_text_cleaned = data.keyword_text.strip().lower()
    if not keyword_text_cleaned:
        return 400, {"message": "Keyword text cannot be empty"}

    keyword, keyword_created = Keyword.objects.get_or_create(keyword_text=keyword_text_cleaned)
    project_keyword, project_keyword_created = ProjectKeyword.objects.get_or_create(
        project=project, keyword=keyword
    )

    if keyword_created:
        keyword.fetch_and_update_metrics()

    message = "Keyword added" if project_keyword_created else "Keyword already added"

    return {
        "status": "success",
        "message": message,
        "keyword": serialize_public_keyword(project_keyword),
    }


@public_api.get(
    "/projects/{project_id}/competitors",
    response={200: PublicCompetitorListOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Competitors"],
)
def list_public_competitors(
    request: HttpRequest,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    competitors_query = Competitor.objects.filter(project=project).order_by("-updated_at", "-created_at")
    total = competitors_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    competitors = list(competitors_query[start_index:end_index])

    return {
        "status": "success",
        "competitors": [
            serialize_public_competitor(competitor) for competitor in competitors
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.get(
    "/projects/{project_id}/competitors/{competitor_id}",
    response={200: PublicCompetitorGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Competitors"],
)
def get_public_competitor(request: HttpRequest, project_id: int, competitor_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    competitor = Competitor.objects.filter(id=competitor_id, project=project).first()
    if competitor is None:
        return 404, {"message": "Competitor not found"}

    return {"status": "success", "competitor": serialize_public_competitor(competitor)}


@public_api.post(
    "/projects/{project_id}/competitors",
    response={
        200: PublicCompetitorCreateOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Competitors"],
)
def create_public_competitor(request: HttpRequest, project_id: int, data: PublicCompetitorCreateIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "competitor analysis")
    if gate_error:
        return 400, {"message": gate_error["message"]}

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    entitlement_error = get_public_entitlement_error(profile, PlanEntitlement.COMPETITOR_ADD)
    if entitlement_error:
        return 403, entitlement_error

    competitor_url = data.url.strip()
    if not competitor_url:
        return 400, {"message": "Competitor URL cannot be empty"}

    if not competitor_url.startswith(("http://", "https://")):
        return 400, {"message": "Competitor URL must start with http:// or https://"}

    existing_competitor = Competitor.objects.filter(project=project, url=competitor_url).first()
    if existing_competitor is not None:
        return {
            "status": "success",
            "message": "Competitor already exists",
            "competitor": serialize_public_competitor(existing_competitor),
        }

    competitor_name = data.name.strip()
    competitor_description = data.description.strip()
    competitor = Competitor.objects.create(
        project=project,
        url=competitor_url,
        name=competitor_name,
        description=competitor_description,
    )

    message = "Competitor added"
    if data.analyze_now:
        try:
            got_content = competitor.get_page_content()
            if got_content:
                competitor.populate_name_description()
            else:
                message = "Competitor added, but failed to get page content"
        except Exception as error:
            logger.warning(
                "[Public API] Failed to analyze newly added competitor",
                error=str(error),
                exc_info=True,
                project_id=project_id,
                profile_id=profile.id,
                competitor_id=competitor.id,
            )
            message = "Competitor added, but analysis failed"

    return {
        "status": "success",
        "message": message,
        "competitor": serialize_public_competitor(competitor),
    }


@public_api.get(
    "/projects/{project_id}/pages",
    response={200: PublicProjectPageListOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Project Pages"],
)
def list_public_project_pages(
    request: HttpRequest,
    project_id: int,
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    pages_query = ProjectPage.objects.filter(project=project).order_by("-date_analyzed", "-created_at")
    total = pages_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    pages = list(pages_query[start_index:end_index])

    return {
        "status": "success",
        "pages": [serialize_public_project_page(project_page) for project_page in pages],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.get(
    "/projects/{project_id}/pages/{page_id}",
    response={200: PublicProjectPageGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Project Pages"],
)
def get_public_project_page(request: HttpRequest, project_id: int, page_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    project_page = ProjectPage.objects.filter(id=page_id, project=project).first()
    if project_page is None:
        return 404, {"message": "Project page not found"}

    return {"status": "success", "page": serialize_public_project_page(project_page)}


@public_api.post(
    "/projects/{project_id}/pages",
    response={200: PublicProjectPageCreateOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Project Pages"],
)
def create_public_project_page(request: HttpRequest, project_id: int, data: PublicProjectPageCreateIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "project page analysis")
    if gate_error:
        return 400, {"message": gate_error["message"]}

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    page_url = data.url.strip()
    if not page_url:
        return 400, {"message": "Page URL cannot be empty"}

    if not page_url.startswith(("http://", "https://")):
        return 400, {"message": "Page URL must start with http:// or https://"}

    project_page, is_created = ProjectPage.objects.get_or_create(
        project=project,
        url=page_url,
    )

    if not is_created:
        return {
            "status": "success",
            "message": "Project page already exists",
            "page": serialize_public_project_page(project_page),
        }

    if data.analyze_now:
        try:
            got_content = project_page.get_page_content()
            if got_content:
                project_page.analyze_content()
        except Exception as error:
            logger.warning(
                "[Public API] Failed to analyze newly added project page",
                error=str(error),
                exc_info=True,
                project_id=project_id,
                profile_id=profile.id,
                project_page_id=project_page.id,
            )

    return {
        "status": "success",
        "message": "Project page added",
        "page": serialize_public_project_page(project_page),
    }


@public_api.post(
    "/projects/{project_id}/executions",
    response={
        200: PublicExecutionJobCreateOut,
        400: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Execution Jobs"],
)
def create_public_execution_job(request: HttpRequest, project_id: int, data: PublicExecutionJobCreateIn):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return execution_error("PROJECT_NOT_FOUND", "Project not found", status_code=404)

    idempotency_key = get_request_idempotency_key(request)
    if not idempotency_key:
        return execution_error(
            "MISSING_IDEMPOTENCY_KEY",
            "Provide an Idempotency-Key header for job creation.",
            status_code=400,
        )

    if data.operation != ExecutionJobOperation.GENERATE_BLOG_POST:
        return execution_error(
            "INVALID_OPERATION",
            "Unsupported operation. Supported operations: GENERATE_BLOG_POST.",
            status_code=400,
        )

    if data.title_suggestion_id is None:
        return execution_error(
            "MISSING_TITLE_SUGGESTION_ID",
            "title_suggestion_id is required for GENERATE_BLOG_POST operation.",
            status_code=400,
        )

    suggestion = BlogPostTitleSuggestion.objects.filter(
        id=data.title_suggestion_id,
        project=project,
    ).first()
    if suggestion is None:
        return execution_error(
            "TITLE_SUGGESTION_NOT_FOUND",
            "Title suggestion not found for this project.",
            status_code=404,
        )

    try:
        with transaction.atomic():
            job, created = AgentExecutionJob.objects.get_or_create(
                profile=profile,
                operation=data.operation,
                idempotency_key=idempotency_key,
                defaults={
                    "project": project,
                    "payload": {"title_suggestion_id": suggestion.id},
                    "status": ExecutionJobStatus.QUEUED,
                },
            )

            if not created and job.project_id != project.id:
                return execution_error(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "Idempotency key already used for a different project or payload.",
                    status_code=400,
                )

            if created:
                queue_task_id = async_task(
                    "core.tasks.run_agent_execution_job",
                    job.id,
                    group="Agent Execution Jobs",
                )
                job.queue_task_id = queue_task_id or ""
                job.save(update_fields=["queue_task_id", "updated_at"])
    except IntegrityError:
        job = AgentExecutionJob.objects.filter(
            profile=profile,
            operation=data.operation,
            idempotency_key=idempotency_key,
        ).first()
        created = False

    if job is None:
        return execution_error(
            "JOB_CREATION_FAILED",
            "Failed to create execution job. Please retry with a new idempotency key.",
            status_code=400,
        )

    return {
        "status": "success",
        "message": "Execution job created" if created else "Existing execution job returned for idempotency key",
        "created": created,
        "job": serialize_public_execution_job(job),
    }


@public_api.get(
    "/executions/{job_id}",
    response={200: PublicExecutionJobGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Execution Jobs"],
)
def get_public_execution_job(request: HttpRequest, job_id: int):
    profile = request.auth
    job = AgentExecutionJob.objects.filter(id=job_id, profile=profile).first()
    if job is None:
        return execution_error("JOB_NOT_FOUND", "Execution job not found", status_code=404)

    return {"status": "success", "job": serialize_public_execution_job(job)}


@public_api.get(
    "/executions",
    response={200: PublicExecutionJobListOut},
    auth=[public_api_key_auth],
    tags=["Execution Jobs"],
)
def list_public_execution_jobs(
    request: HttpRequest,
    project_id: int | None = None,
    operation: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    jobs_query = AgentExecutionJob.objects.filter(profile=profile)

    if project_id is not None:
        jobs_query = jobs_query.filter(project_id=project_id)

    if operation:
        jobs_query = jobs_query.filter(operation=operation)

    if status:
        normalized_status = status.upper()
        valid_statuses = {choice[0] for choice in ExecutionJobStatus.choices}
        if normalized_status in valid_statuses:
            jobs_query = jobs_query.filter(status=normalized_status)

    jobs_query = jobs_query.order_by("-created_at")

    total = jobs_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    jobs = list(jobs_query[start_index:end_index])

    return {
        "status": "success",
        "jobs": [serialize_public_execution_job(job) for job in jobs],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.post(
    "/executions/{job_id}/cancel",
    response={200: PublicExecutionJobActionOut, 404: PublicAPIErrorOut, 400: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Execution Jobs"],
)
def cancel_public_execution_job(request: HttpRequest, job_id: int):
    profile = request.auth

    with transaction.atomic():
        job = AgentExecutionJob.objects.select_for_update().filter(id=job_id, profile=profile).first()
        if job is None:
            return execution_error("JOB_NOT_FOUND", "Execution job not found", status_code=404)

        if job.status in {ExecutionJobStatus.SUCCEEDED, ExecutionJobStatus.FAILED}:
            return execution_error(
                "JOB_ALREADY_TERMINAL",
                "Only queued or running jobs can be canceled.",
                status_code=400,
            )

        if job.status == ExecutionJobStatus.CANCELED:
            return {
                "status": "success",
                "message": "Execution job already canceled",
                "job": serialize_public_execution_job(job),
            }

        job.status = ExecutionJobStatus.CANCELED
        job.canceled_at = timezone.now()
        job.completed_at = job.completed_at or job.canceled_at
        job.canceled_reason = "Canceled via Public API"
        job.error_code = "CANCELED"
        job.error_message = "Execution canceled by user request"
        job.save(
            update_fields=[
                "status",
                "canceled_at",
                "completed_at",
                "canceled_reason",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

    return {
        "status": "success",
        "message": "Execution job canceled",
        "job": serialize_public_execution_job(job),
    }


@public_api.post(
    "/executions/{job_id}/retry",
    response={200: PublicExecutionJobActionOut, 404: PublicAPIErrorOut, 400: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Execution Jobs"],
)
def retry_public_execution_job(request: HttpRequest, job_id: int):
    profile = request.auth

    base_job = AgentExecutionJob.objects.filter(id=job_id, profile=profile).first()
    if base_job is None:
        return execution_error("JOB_NOT_FOUND", "Execution job not found", status_code=404)

    if base_job.status not in {ExecutionJobStatus.FAILED, ExecutionJobStatus.CANCELED}:
        return execution_error(
            "RETRY_NOT_ALLOWED",
            "Only failed or canceled jobs can be retried.",
            status_code=400,
        )

    idempotency_key = get_request_idempotency_key(request)
    if not idempotency_key:
        return execution_error(
            "MISSING_IDEMPOTENCY_KEY",
            "Provide an Idempotency-Key header when retrying a job.",
            status_code=400,
        )

    with transaction.atomic():
        retry_job, created = AgentExecutionJob.objects.get_or_create(
            profile=profile,
            operation=base_job.operation,
            idempotency_key=idempotency_key,
            defaults={
                "project": base_job.project,
                "payload": base_job.payload,
                "status": ExecutionJobStatus.QUEUED,
                "retry_of": base_job,
            },
        )

        if created:
            queue_task_id = async_task(
                "core.tasks.run_agent_execution_job",
                retry_job.id,
                group="Agent Execution Jobs",
            )
            retry_job.queue_task_id = queue_task_id or ""
            retry_job.save(update_fields=["queue_task_id", "updated_at"])

    return {
        "status": "success",
        "message": "Execution job retried" if created else "Existing retry job returned for idempotency key",
        "job": serialize_public_execution_job(retry_job),
    }


@public_api.post(
    "/projects/{project_id}/blog-posts/generate",
    response={
        200: PublicBlogPostGenerateOut,
        400: PublicAPIErrorOut,
        403: PublicAPIErrorOut,
        404: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Blog Posts"],
)
def generate_public_blog_post(request: HttpRequest, project_id: int, data: PublicBlogPostGenerateIn):
    profile = request.auth

    gate_error = get_verified_email_gate_error(profile, "blog content generation")
    if gate_error:
        return 400, {"message": gate_error["message"]}

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    entitlement_error = get_public_entitlement_error(profile, PlanEntitlement.CONTENT_GENERATION)
    if entitlement_error:
        return 403, entitlement_error

    suggestion = BlogPostTitleSuggestion.objects.filter(
        id=data.title_suggestion_id, project=project
    ).first()
    if suggestion is None:
        return 404, {"message": "Title suggestion not found"}

    try:
        content_type = ContentType[suggestion.content_type]
    except KeyError:
        return 400, {"message": f"Invalid content type on suggestion: {suggestion.content_type}"}

    try:
        blog_post = suggestion.generate_content(content_type=content_type)
    except ValueError as error:
        return 400, {"message": str(error)}
    except Exception as error:
        logger.error(
            "[Public API] Failed to generate blog post",
            error=str(error),
            exc_info=True,
            project_id=project_id,
            profile_id=profile.id,
            suggestion_id=suggestion.id,
        )
        return 400, {"message": "Failed to generate blog post"}

    return {
        "status": "success",
        "message": "Blog post generated",
        "post": serialize_public_blog_post(blog_post),
    }


@public_api.get(
    "/projects/{project_id}/blog-posts",
    response={200: PublicBlogPostListOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Blog Posts"],
)
def list_public_blog_posts(
    request: HttpRequest,
    project_id: int,
    include_content: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    posts_query = GeneratedBlogPost.objects.filter(project=project).order_by("-created_at")
    total = posts_query.count()
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    posts = list(posts_query[start_index:end_index])

    return {
        "status": "success",
        "posts": [
            serialize_public_blog_post(post, include_content=include_content) for post in posts
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@public_api.get(
    "/projects/{project_id}/blog-posts/{blog_post_id}",
    response={200: PublicBlogPostGetOut, 404: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Blog Posts"],
)
def get_public_blog_post(request: HttpRequest, project_id: int, blog_post_id: int):
    profile = request.auth
    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    post = GeneratedBlogPost.objects.filter(id=blog_post_id, project=project).first()
    if post is None:
        return 404, {"message": "Blog post not found"}

    return {
        "status": "success",
        "post": serialize_public_blog_post(
            post,
            include_link_audit=True,
            include_workflow_audit=True,
        ),
    }


@public_api.post(
    "/projects/{project_id}/blog-posts/{blog_post_id}/review",
    response={200: PublicBlogPostApprovalReviewOut, 404: PublicAPIErrorOut, 400: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Blog Posts"],
)
def review_public_blog_post(
    request: HttpRequest,
    project_id: int,
    blog_post_id: int,
    data: PublicBlogPostApprovalReviewIn,
):
    profile = request.auth

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    post = GeneratedBlogPost.objects.filter(id=blog_post_id, project=project).first()
    if post is None:
        return 404, {"message": "Blog post not found"}

    checkpoint = (data.checkpoint or "").strip().lower()
    decision = (data.decision or "").strip().lower()

    valid_checkpoints = {"publish", "external_links"}
    valid_decisions = {"approve", "reject", "request_changes"}

    if checkpoint not in valid_checkpoints:
        return 400, {"message": "Invalid checkpoint. Use one of: publish, external_links"}

    if decision not in valid_decisions:
        return 400, {"message": "Invalid decision. Use one of: approve, reject, request_changes"}

    post.apply_approval_decision(
        checkpoint=checkpoint,
        decision=decision,
        actor_profile=profile,
        reason=data.reason,
    )

    return {
        "status": "success",
        "message": "Review decision recorded",
        "post": serialize_public_blog_post(post),
    }


@public_api.post(
    "/projects/{project_id}/blog-posts/{blog_post_id}/publish",
    response={200: PublicBlogPostPublishOut, 404: PublicAPIErrorOut, 400: PublicAPIErrorOut},
    auth=[public_api_key_auth],
    tags=["Blog Posts"],
)
def publish_public_blog_post(request: HttpRequest, project_id: int, blog_post_id: int):
    profile = request.auth

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    post = GeneratedBlogPost.objects.filter(id=blog_post_id, project=project).first()
    if post is None:
        return 404, {"message": "Blog post not found"}

    if post.posted:
        return {
            "status": "success",
            "message": "Blog post already published",
            "post": serialize_public_blog_post(post),
        }

    if post.publish_approval_status != GeneratedBlogPost.ApprovalStatus.APPROVED:
        post.create_workflow_audit_event(
            checkpoint="PUBLISH",
            event_type="ACTION_BLOCKED",
            actor_profile=profile,
            decision=post.publish_approval_status,
            reason="awaiting_publish_approval",
        )
        return 400, {
            "message": (
                "Publish blocked by approval checkpoint: "
                f"current_status={post.publish_approval_status}"
            )
        }

    quality_gate_result = evaluate_pre_publish_quality_gate(post)
    if quality_gate_result["decision"] == "block":
        logger.warning(
            "[Public API Publish Quality Gate] Blocking publish attempt",
            profile_id=profile.id,
            project_id=project_id,
            blog_post_id=blog_post_id,
            checks=quality_gate_result["blocking_checks"],
        )
        post.create_workflow_audit_event(
            checkpoint="PUBLISH",
            event_type="QUALITY_GATE_BLOCKED",
            actor_profile=profile,
            decision="BLOCKED",
            reason=quality_gate_result["summary"],
            metadata={"blocking_checks": quality_gate_result["blocking_checks"]},
        )
        return 400, {"message": f"Publish blocked by quality gate: {quality_gate_result['summary']}"}

    if quality_gate_result["decision"] == "warn":
        logger.warning(
            "[Public API Publish Quality Gate] Publish allowed with warnings",
            profile_id=profile.id,
            project_id=project_id,
            blog_post_id=blog_post_id,
            checks=quality_gate_result["warning_checks"],
            aggregate_score=quality_gate_result["aggregate_score"],
        )

    submitted = post.submit_blog_post_to_endpoint()
    if not submitted:
        post.create_workflow_audit_event(
            checkpoint="PUBLISH",
            event_type="PUBLISH_FAILED",
            actor_profile=profile,
            decision="FAILED",
            reason="endpoint_submission_failed",
        )
        return 400, {"message": "Failed to publish blog post"}

    post.posted = True
    post.date_posted = post.date_posted or timezone.now()
    post.save(update_fields=["posted", "date_posted"])

    if quality_gate_result["decision"] == "warn":
        publish_message = f"Blog post published with quality warnings: {quality_gate_result['summary']}"
    else:
        publish_message = "Blog post published"

    post.create_workflow_audit_event(
        checkpoint="PUBLISH",
        event_type="PUBLISHED",
        actor_profile=profile,
        decision="SUCCESS",
        reason=publish_message,
        metadata={"quality_gate_decision": quality_gate_result["decision"]},
    )

    return {
        "status": "success",
        "message": publish_message,
        "post": serialize_public_blog_post(post),
    }
