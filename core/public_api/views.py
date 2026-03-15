from django.http import HttpRequest
from django.utils import timezone
from django_q.tasks import async_task
from ninja import NinjaAPI

from core.abuse_prevention import enforce_verified_email_for_expensive_action
from core.choices import ContentType
from core.models import (
    AutoSubmissionSetting,
    BlogPostTitleSuggestion,
    GeneratedBlogPost,
    Keyword,
    Project,
    ProjectKeyword,
)
from core.public_api.auth import public_api_key_auth
from core.public_api.schemas import (
    PublicAPIErrorOut,
    PublicAccountOut,
    PublicBlogPostGenerateIn,
    PublicBlogPostGenerateOut,
    PublicBlogPostGetOut,
    PublicBlogPostListOut,
    PublicBlogPostPublishOut,
    PublicContentAutomationIn,
    PublicContentAutomationOut,
    PublicKeywordCreateIn,
    PublicKeywordCreateOut,
    PublicKeywordGetOut,
    PublicKeywordListOut,
    PublicProjectCreateOut,
    PublicProjectGetOut,
    PublicProjectIn,
    PublicProjectUpdateIn,
    PublicProjectUpdateOut,
    PublicTitleSuggestionCreateIn,
    PublicTitleSuggestionCreateOut,
    PublicTitleSuggestionGetOut,
    PublicTitleSuggestionListOut,
)
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)

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


def serialize_public_blog_post(blog_post: GeneratedBlogPost, *, include_content: bool = True) -> dict:
    return {
        "id": blog_post.id,
        "title": blog_post.title,
        "slug": blog_post.slug,
        "description": blog_post.description,
        "tags": blog_post.tags,
        "posted": blog_post.posted,
        "date_posted": blog_post.date_posted.isoformat() if blog_post.date_posted else None,
        "title_suggestion_id": blog_post.title_suggestion_id,
        "content": blog_post.content if include_content else None,
    }


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


@public_api.post(
    "/projects",
    response={200: PublicProjectCreateOut, 400: PublicAPIErrorOut, 500: PublicAPIErrorOut},
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
            return 400, {"message": limit_message}

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

        async_task(
            "core.tasks.auto_discover_and_ingest_sitemap",
            project.id,
            group="Discover Sitemap",
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
        404: PublicAPIErrorOut,
        500: PublicAPIErrorOut,
    },
    auth=[public_api_key_auth],
    tags=["Content Automation"],
)
def configure_content_automation(
    request: HttpRequest, project_id: int, data: PublicContentAutomationIn
):
    profile = request.auth

    project = Project.objects.filter(id=project_id, profile=profile).first()
    if project is None:
        return 404, {"message": "Project not found"}

    if not profile.is_on_pro_plan:
        return 400, {
            "message": "Automatic Post Submission is only available on the Pro plan."
        }

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
    response={200: PublicTitleSuggestionCreateOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
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
    response={200: PublicKeywordCreateOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
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
        else:
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


@public_api.post(
    "/projects/{project_id}/blog-posts/generate",
    response={200: PublicBlogPostGenerateOut, 400: PublicAPIErrorOut, 404: PublicAPIErrorOut},
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

    return {"status": "success", "post": serialize_public_blog_post(post)}


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

    submitted = post.submit_blog_post_to_endpoint()
    if not submitted:
        return 400, {"message": "Failed to publish blog post"}

    post.posted = True
    post.date_posted = post.date_posted or timezone.now()
    post.save(update_fields=["posted", "date_posted"])

    return {
        "status": "success",
        "message": "Blog post published",
        "post": serialize_public_blog_post(post),
    }
