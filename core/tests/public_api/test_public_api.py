from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import ValidationError

from core.api.views import api
from core.public_api.auth import PublicAPIKeyAuth
from core.public_api.schemas import (
    PublicBlogPostGenerateIn,
    PublicContentAutomationIn,
    PublicKeywordCreateIn,
    PublicProjectIn,
    PublicProjectUpdateIn,
    PublicTitleSuggestionCreateIn,
)
from core.public_api.views import (
    configure_content_automation,
    create_public_keyword,
    create_public_project,
    create_public_title_suggestions,
    generate_public_blog_post,
    get_public_blog_post,
    get_public_keyword,
    get_public_project,
    get_public_title_suggestion,
    list_public_blog_posts,
    list_public_keywords,
    list_public_title_suggestions,
    publish_public_blog_post,
    public_api,
    update_public_project,
)


def build_profile(**overrides):
    default_profile = {
        "id": 1,
        "user": SimpleNamespace(email="public-api-user@example.com"),
        "product_name": "Free",
        "is_on_pro_plan": False,
        "project_limit": 1,
        "number_of_active_projects": 0,
        "can_create_project": True,
        "is_on_free_plan": True,
    }
    default_profile.update(overrides)
    return SimpleNamespace(**default_profile)


def test_public_api_key_auth_returns_profile_for_valid_key():
    expected_profile = build_profile()

    with patch("core.public_api.auth.Profile.objects.get", return_value=expected_profile):
        authenticated_profile = PublicAPIKeyAuth().authenticate(request=None, key="valid-key")

    assert authenticated_profile.id == expected_profile.id


def test_public_api_key_auth_returns_none_for_invalid_key():
    from core.public_api import auth

    with patch(
        "core.public_api.auth.Profile.objects.get",
        side_effect=auth.Profile.DoesNotExist,
    ):
        authenticated_profile = PublicAPIKeyAuth().authenticate(request=None, key="invalid-key")

    assert authenticated_profile is None


def test_public_project_schema_requires_url_field():
    with pytest.raises(ValidationError):
        PublicProjectIn.model_validate({})


def test_public_content_automation_schema_requires_positive_posts_per_month():
    with pytest.raises(ValidationError):
        PublicContentAutomationIn.model_validate(
            {"endpoint_url": "https://example.com/publish", "posts_per_month": 0}
        )


def test_public_title_suggestion_create_schema_requires_positive_count():
    with pytest.raises(ValidationError):
        PublicTitleSuggestionCreateIn.model_validate({"count": 0})


def test_public_keyword_create_schema_requires_keyword_text():
    with pytest.raises(ValidationError):
        PublicKeywordCreateIn.model_validate({})


def test_create_public_project_returns_error_for_invalid_url_scheme():
    request = SimpleNamespace(auth=build_profile())

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        response_status_code, response_data = create_public_project(
            request,
            PublicProjectIn(url="example.com", source="public_api"),
        )

    assert response_status_code == 400
    assert response_data["message"] == "Project URL must start with http:// or https://"


def test_create_public_project_returns_error_for_duplicate_project_url():
    request = SimpleNamespace(auth=build_profile())

    project_filter_mock = Mock()
    project_filter_mock.exists.return_value = True

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        with patch(
            "core.public_api.views.Project.objects.filter",
            return_value=project_filter_mock,
        ):
            response_status_code, response_data = create_public_project(
                request,
                PublicProjectIn(url="https://example.com", source="public_api"),
            )

    assert response_status_code == 400
    assert response_data["message"] == "You already added this project URL"


def test_create_public_project_returns_success():
    project_mock = Mock()
    project_mock.id = 10
    project_mock.name = "Project Name"
    project_mock.url = "https://example.com"
    project_mock.summary = "Summary"
    project_mock.get_type_display.return_value = "SaaS"
    project_mock.get_page_content.return_value = True
    project_mock.analyze_content.return_value = True

    profile = build_profile(get_or_create_project=Mock(return_value=project_mock))
    request = SimpleNamespace(auth=profile)

    project_filter_mock = Mock()
    project_filter_mock.exists.return_value = False

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        with patch(
            "core.public_api.views.Project.objects.filter",
            return_value=project_filter_mock,
        ):
            with patch("core.public_api.views.async_task") as mock_async_task:
                response_data = create_public_project(
                    request,
                    PublicProjectIn(url="https://example.com", source="public_api"),
                )

    assert response_data["status"] == "success"
    assert response_data["project"]["project_id"] == 10
    mock_async_task.assert_called_once_with(
        "core.tasks.auto_discover_and_ingest_sitemap",
        project_mock.id,
        group="Discover Sitemap",
    )


def test_get_public_project_returns_not_found_for_missing_project():
    request = SimpleNamespace(auth=build_profile())

    project_filter_mock = Mock()
    project_filter_mock.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_status_code, response_data = get_public_project(request, project_id=10)

    assert response_status_code == 404
    assert response_data["message"] == "Project not found"


def test_get_public_project_returns_success():
    project_mock = Mock()
    project_mock.id = 10
    project_mock.name = "Project Name"
    project_mock.url = "https://example.com"
    project_mock.summary = "Summary"
    project_mock.get_type_display.return_value = "SaaS"
    project_mock.blog_theme = "Founder-focused growth"
    project_mock.founders = "Jane Doe"
    project_mock.key_features = "SEO automation"
    project_mock.target_audience_summary = "Bootstrapped SaaS founders"
    project_mock.pain_points = "No time for content"
    project_mock.product_usage = "Weekly blog generation"
    project_mock.links = "https://example.com/docs"
    project_mock.language = "english"
    project_mock.location = "Global"

    request = SimpleNamespace(auth=build_profile())
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_data = get_public_project(request, project_id=10)

    assert response_data["status"] == "success"
    assert response_data["project"]["project_id"] == 10
    assert response_data["project"]["name"] == "Project Name"


def test_update_public_project_returns_not_found_for_missing_project():
    request = SimpleNamespace(auth=build_profile())

    project_filter_mock = Mock()
    project_filter_mock.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_status_code, response_data = update_public_project(
            request,
            project_id=10,
            data=PublicProjectUpdateIn(name="Updated"),
        )

    assert response_status_code == 404
    assert response_data["message"] == "Project not found"


def test_update_public_project_returns_error_when_no_fields_are_provided():
    request = SimpleNamespace(auth=build_profile())
    project_mock = Mock()
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_status_code, response_data = update_public_project(
            request,
            project_id=10,
            data=PublicProjectUpdateIn(),
        )

    assert response_status_code == 400
    assert response_data["message"] == "At least one field is required for update"


def test_update_public_project_updates_only_provided_fields():
    request = SimpleNamespace(auth=build_profile())
    project_mock = Mock()
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_data = update_public_project(
            request,
            project_id=10,
            data=PublicProjectUpdateIn(name="Updated Project", summary="Updated summary"),
        )

    assert response_data["status"] == "success"
    assert response_data["project"]["name"] == "Updated Project"
    assert response_data["project"]["summary"] == "Updated summary"
    project_mock.save.assert_called_once_with(update_fields=["name", "summary"])


def test_configure_content_automation_returns_not_found_for_missing_project():
    request = SimpleNamespace(auth=build_profile())

    project_filter_mock = Mock()
    project_filter_mock.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_status_code, response_data = configure_content_automation(
            request,
            project_id=10,
            data=PublicContentAutomationIn(endpoint_url="https://example.com/publish"),
        )

    assert response_status_code == 404
    assert response_data["message"] == "Project not found"


def test_configure_content_automation_returns_plan_error_for_free_profile():
    project_mock = Mock()
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    request = SimpleNamespace(auth=build_profile(is_on_pro_plan=False))

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        response_status_code, response_data = configure_content_automation(
            request,
            project_id=10,
            data=PublicContentAutomationIn(endpoint_url="https://example.com/publish"),
        )

    assert response_status_code == 400
    assert "Pro plan" in response_data["message"]


def test_configure_content_automation_returns_success_for_pro_profile():
    project_mock = Mock()
    project_mock.id = 10
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    profile = build_profile(is_on_pro_plan=True)
    request = SimpleNamespace(auth=profile)

    content_automation_mock = Mock()
    content_automation_mock.id = 22

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        with patch(
            "core.public_api.views.AutoSubmissionSetting.objects.update_or_create",
            return_value=(content_automation_mock, True),
        ):
            response_data = configure_content_automation(
                request,
                project_id=10,
                data=PublicContentAutomationIn(
                    endpoint_url="https://example.com/publish",
                    request_body_json={"title": "{{title}}"},
                    request_headers_json={"Authorization": "Bearer token"},
                    posts_per_month=2,
                    enable_automatic_post_submission=True,
                ),
            )

    assert response_data["status"] == "success"
    assert response_data["content_automation_id"] == 22


def test_configure_content_automation_returns_error_when_save_fails():
    project_mock = Mock()
    project_filter_mock = Mock()
    project_filter_mock.first.return_value = project_mock

    profile = build_profile(is_on_pro_plan=True)
    request = SimpleNamespace(auth=profile)

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter_mock):
        with patch(
            "core.public_api.views.AutoSubmissionSetting.objects.update_or_create",
            side_effect=RuntimeError("database failure"),
        ):
            response_status_code, response_data = configure_content_automation(
                request,
                project_id=10,
                data=PublicContentAutomationIn(endpoint_url="https://example.com/publish"),
            )

    assert response_status_code == 500
    assert response_data["message"] == "Failed to save content automation settings"


def _build_title_suggestion(id_value: int, *, archived: bool, published: bool):
    suggestion = Mock()
    suggestion.id = id_value
    suggestion.title = f"Suggestion {id_value}"
    suggestion.category = "General Audience"
    suggestion.description = f"Description {id_value}"
    suggestion.target_keywords = []
    suggestion.suggested_meta_description = f"Meta {id_value}"
    suggestion.content_type = "SHARING"
    suggestion.archived = archived
    generated_posts_filter = Mock()
    generated_posts_filter.exists.return_value = published
    suggestion.generated_blog_posts.filter.return_value = generated_posts_filter
    return suggestion


def test_list_public_title_suggestions_supports_filters_and_pagination():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project

    unpublished = _build_title_suggestion(1, archived=False, published=False)
    published = _build_title_suggestion(2, archived=False, published=True)
    archived = _build_title_suggestion(3, archived=True, published=False)
    suggestions = [unpublished, published, archived]

    suggestion_query = MagicMock()
    suggestion_query.filter.return_value = suggestion_query
    suggestion_query.exclude.return_value = suggestion_query
    suggestion_query.distinct.return_value = suggestion_query
    suggestion_query.order_by.return_value = suggestion_query
    suggestion_query.count.return_value = len(suggestions)
    suggestion_query.__getitem__.return_value = suggestions[:2]

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch(
            "core.public_api.views.BlogPostTitleSuggestion.objects.filter",
            return_value=suggestion_query,
        ):
            all_response = list_public_title_suggestions(
                request,
                project_id=project.id,
                status="all",
                page=1,
                page_size=2,
            )

            published_response = list_public_title_suggestions(
                request,
                project_id=project.id,
                status="published",
                page=1,
                page_size=10,
            )

            archived_response = list_public_title_suggestions(
                request,
                project_id=project.id,
                status="archived",
                page=1,
                page_size=10,
            )

            unpublished_response = list_public_title_suggestions(
                request,
                project_id=project.id,
                status="unpublished",
                page=1,
                page_size=10,
            )

    assert all_response["pagination"]["total"] == 3
    assert len(all_response["suggestions"]) == 2
    assert all_response["suggestions"][0]["status"] == "unpublished"
    assert all_response["suggestions"][1]["status"] == "published"
    assert published_response["pagination"]["page"] == 1
    assert archived_response["pagination"]["page_size"] == 10
    assert unpublished_response["pagination"]["total"] == 3
    suggestion_query.filter.assert_any_call(archived=True)
    suggestion_query.filter.assert_any_call(archived=False, generated_blog_posts__posted=True)
    suggestion_query.filter.assert_any_call(archived=False)
    suggestion_query.exclude.assert_called_with(generated_blog_posts__posted=True)


def test_list_public_title_suggestions_returns_not_found_for_non_owned_project():
    request = SimpleNamespace(auth=build_profile())
    project_filter = Mock()
    project_filter.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        response_status_code, response_data = list_public_title_suggestions(
            request,
            project_id=99,
            status="all",
            page=1,
            page_size=10,
        )

    assert response_status_code == 404
    assert response_data["message"] == "Project not found"


def test_get_public_title_suggestion_respects_ownership():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project
    suggestion_filter = Mock()
    suggestion_filter.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch(
            "core.public_api.views.BlogPostTitleSuggestion.objects.filter",
            return_value=suggestion_filter,
        ):
            response_status_code, response_data = get_public_title_suggestion(
                request,
                project_id=project.id,
                suggestion_id=123,
            )

    assert response_status_code == 404
    assert response_data["message"] == "Title suggestion not found"


def test_create_public_title_suggestions_passes_count_and_seed_guidance():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project
    generated_suggestion = _build_title_suggestion(44, archived=False, published=False)
    project.generate_title_suggestions.return_value = [generated_suggestion]

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        response_data = create_public_title_suggestions(
            request,
            project_id=project.id,
            data=PublicTitleSuggestionCreateIn(count=2, seed_guidance="focus on onboarding"),
        )

    assert response_data["status"] == "success"
    assert response_data["count"] == 1
    assert response_data["suggestions"][0]["id"] == generated_suggestion.id
    project.generate_title_suggestions.assert_called_once()
    assert project.generate_title_suggestions.call_args.kwargs["num_titles"] == 2
    assert (
        project.generate_title_suggestions.call_args.kwargs["user_prompt"]
        == "focus on onboarding"
    )


def _build_project_keyword(*, keyword_id: int, project_keyword_id: int, keyword_text: str):
    keyword = Mock()
    keyword.id = keyword_id
    keyword.keyword_text = keyword_text
    keyword.volume = 320
    keyword.cpc_currency = "usd"
    keyword.cpc_value = 4.25
    keyword.competition = 0.45
    keyword.country = "us"
    keyword.data_source = "gkp"
    keyword.last_fetched_at = SimpleNamespace(isoformat=Mock(return_value="2026-03-13T00:00:00Z"))
    trend = Mock(value=12, month="Jan", year=2026)
    keyword.trends.all.return_value = [trend]

    project_keyword = Mock()
    project_keyword.id = project_keyword_id
    project_keyword.keyword = keyword
    project_keyword.use = True
    return project_keyword


def test_list_public_keywords_supports_pagination():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project

    first_project_keyword = _build_project_keyword(
        keyword_id=1, project_keyword_id=11, keyword_text="onboarding seo"
    )
    second_project_keyword = _build_project_keyword(
        keyword_id=2, project_keyword_id=12, keyword_text="saas content"
    )
    project_keywords = [first_project_keyword, second_project_keyword]

    keyword_query = MagicMock()
    keyword_query.select_related.return_value = keyword_query
    keyword_query.order_by.return_value = keyword_query
    keyword_query.count.return_value = len(project_keywords)
    keyword_query.__getitem__.return_value = project_keywords[:1]

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch("core.public_api.views.ProjectKeyword.objects.filter", return_value=keyword_query):
            response_data = list_public_keywords(request, project_id=project.id, page=1, page_size=1)

    assert response_data["status"] == "success"
    assert response_data["pagination"]["total"] == 2
    assert len(response_data["keywords"]) == 1
    assert response_data["keywords"][0]["keyword_text"] == "onboarding seo"


def test_get_public_keyword_returns_not_found_for_non_owned_project():
    request = SimpleNamespace(auth=build_profile())
    project_filter = Mock()
    project_filter.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        response_status_code, response_data = get_public_keyword(
            request,
            project_id=99,
            keyword_id=1,
        )

    assert response_status_code == 404
    assert response_data["message"] == "Project not found"


def test_get_public_keyword_returns_keyword_details():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project
    project_keyword = _build_project_keyword(
        keyword_id=4, project_keyword_id=44, keyword_text="content ops"
    )
    project_keyword_query = MagicMock()
    project_keyword_query.filter.return_value = project_keyword_query
    project_keyword_query.first.return_value = project_keyword

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch(
            "core.public_api.views.ProjectKeyword.objects.select_related",
            return_value=project_keyword_query,
        ):
            response_data = get_public_keyword(request, project_id=project.id, keyword_id=4)

    assert response_data["status"] == "success"
    assert response_data["keyword"]["id"] == 4
    assert response_data["keyword"]["project_keyword_id"] == 44


def test_create_public_keyword_returns_error_for_blank_text():
    request = SimpleNamespace(auth=build_profile(can_add_keywords=True))
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
            response_status_code, response_data = create_public_keyword(
                request,
                project_id=project.id,
                data=PublicKeywordCreateIn(keyword_text="   "),
            )

    assert response_status_code == 400
    assert response_data["message"] == "Keyword text cannot be empty"


def test_create_public_keyword_normalizes_keyword_text():
    profile = build_profile(can_add_keywords=True, is_on_free_plan=False)
    request = SimpleNamespace(auth=profile)
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project

    keyword = Mock()
    keyword.id = 5
    keyword.keyword_text = "seo automation"
    keyword.volume = None
    keyword.cpc_currency = ""
    keyword.cpc_value = None
    keyword.competition = None
    keyword.country = "us"
    keyword.data_source = "gkp"
    keyword.last_fetched_at = None
    keyword.trends.all.return_value = []
    keyword.fetch_and_update_metrics = Mock(return_value=True)

    project_keyword = Mock()
    project_keyword.id = 15
    project_keyword.keyword = keyword
    project_keyword.use = False

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
            with patch(
                "core.public_api.views.Keyword.objects.get_or_create",
                return_value=(keyword, True),
            ) as keyword_get_or_create:
                with patch(
                    "core.public_api.views.ProjectKeyword.objects.get_or_create",
                    return_value=(project_keyword, True),
                ):
                    response_data = create_public_keyword(
                        request,
                        project_id=project.id,
                        data=PublicKeywordCreateIn(keyword_text="  SEO Automation  "),
                    )

    assert response_data["status"] == "success"
    assert response_data["message"] == "Keyword added"
    assert response_data["keyword"]["keyword_text"] == "seo automation"
    keyword_get_or_create.assert_called_once_with(keyword_text="seo automation")
    keyword.fetch_and_update_metrics.assert_called_once()


def _build_generated_post(*, post_id: int, title_suggestion_id: int | None = None, posted: bool = False):
    post = Mock()
    post.id = post_id
    post.title = f"Generated post {post_id}"
    post.slug = f"generated-post-{post_id}"
    post.description = "Generated description"
    post.tags = "seo,automation"
    post.content = "Generated content"
    post.posted = posted
    post.date_posted = None
    post.title_suggestion_id = title_suggestion_id
    post.save = Mock()
    return post


def test_generate_public_blog_post_from_title_suggestion():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    suggestion = Mock(id=50, content_type="SHARING")
    generated_post = _build_generated_post(post_id=200, title_suggestion_id=suggestion.id)
    suggestion.generate_content.return_value = generated_post

    project_filter = Mock()
    project_filter.first.return_value = project
    suggestion_filter = Mock()
    suggestion_filter.first.return_value = suggestion

    with patch("core.public_api.views.get_verified_email_gate_error", return_value=None):
        with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
            with patch(
                "core.public_api.views.BlogPostTitleSuggestion.objects.filter",
                return_value=suggestion_filter,
            ):
                response_data = generate_public_blog_post(
                    request,
                    project_id=project.id,
                    data=PublicBlogPostGenerateIn(title_suggestion_id=suggestion.id),
                )

    assert response_data["status"] == "success"
    assert response_data["post"]["id"] == generated_post.id
    suggestion.generate_content.assert_called_once()


def test_list_public_blog_posts_hides_content_by_default():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    project_filter = Mock()
    project_filter.first.return_value = project

    generated_posts = [_build_generated_post(post_id=1), _build_generated_post(post_id=2)]

    posts_query = MagicMock()
    posts_query.order_by.return_value = posts_query
    posts_query.count.return_value = len(generated_posts)
    posts_query.__getitem__.return_value = generated_posts

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch(
            "core.public_api.views.GeneratedBlogPost.objects.filter",
            return_value=posts_query,
        ):
            response_data = list_public_blog_posts(request, project_id=project.id, page=1, page_size=20)

    assert response_data["status"] == "success"
    assert response_data["posts"][0]["content"] is None
    assert response_data["pagination"]["total"] == 2


def test_get_public_blog_post_returns_not_found_when_missing():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)

    project_filter = Mock()
    project_filter.first.return_value = project

    post_filter = Mock()
    post_filter.first.return_value = None

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch("core.public_api.views.GeneratedBlogPost.objects.filter", return_value=post_filter):
            response_status_code, response_data = get_public_blog_post(
                request,
                project_id=project.id,
                blog_post_id=999,
            )

    assert response_status_code == 404
    assert response_data["message"] == "Blog post not found"


def test_publish_public_blog_post_sets_posted_flag_when_submission_succeeds():
    request = SimpleNamespace(auth=build_profile())
    project = Mock(id=10)
    post = _build_generated_post(post_id=300)
    post.submit_blog_post_to_endpoint.return_value = True

    project_filter = Mock()
    project_filter.first.return_value = project

    post_filter = Mock()
    post_filter.first.return_value = post

    with patch("core.public_api.views.Project.objects.filter", return_value=project_filter):
        with patch("core.public_api.views.GeneratedBlogPost.objects.filter", return_value=post_filter):
            response_data = publish_public_blog_post(
                request,
                project_id=project.id,
                blog_post_id=post.id,
            )

    assert response_data["status"] == "success"
    assert response_data["post"]["posted"] is True
    post.save.assert_called_once()


def test_public_openapi_includes_public_routes_only():
    openapi_schema = public_api.get_openapi_schema()
    schema_paths = openapi_schema["paths"]

    assert "/public-api/account" in schema_paths
    assert "/public-api/projects" in schema_paths
    assert "/public-api/projects/{project_id}" in schema_paths
    assert "/public-api/projects/{project_id}/content-automation" in schema_paths
    assert "/public-api/projects/{project_id}/title-suggestions" in schema_paths
    assert "/public-api/projects/{project_id}/title-suggestions/{suggestion_id}" in schema_paths
    assert "/public-api/projects/{project_id}/keywords" in schema_paths
    assert "/public-api/projects/{project_id}/keywords/{keyword_id}" in schema_paths
    assert "/public-api/projects/{project_id}/blog-posts/generate" in schema_paths
    assert "/public-api/projects/{project_id}/blog-posts" in schema_paths
    assert "/public-api/projects/{project_id}/blog-posts/{blog_post_id}" in schema_paths
    assert "/public-api/projects/{project_id}/blog-posts/{blog_post_id}/publish" in schema_paths
    assert "/public-api/validate-url" not in schema_paths


def test_public_openapi_groups_endpoints_by_functional_tags():
    openapi_schema = public_api.get_openapi_schema()
    paths = openapi_schema["paths"]

    assert paths["/public-api/account"]["get"]["tags"] == ["Account"]
    assert paths["/public-api/projects"]["post"]["tags"] == ["Projects"]
    assert paths["/public-api/projects/{project_id}/content-automation"]["post"]["tags"] == [
        "Content Automation"
    ]
    assert paths["/public-api/projects/{project_id}/title-suggestions"]["get"]["tags"] == [
        "Title Suggestions"
    ]
    assert paths["/public-api/projects/{project_id}/keywords"]["get"]["tags"] == ["Keywords"]
    assert paths["/public-api/projects/{project_id}/blog-posts"]["get"]["tags"] == [
        "Blog Posts"
    ]


def test_internal_openapi_is_not_exposed():
    assert api.openapi_url is None
