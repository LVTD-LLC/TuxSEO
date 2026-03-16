from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    BlogPostWorkflowAuditLog,
    GeneratedBlogPost,
    LinkOpportunityAuditLog,
    OutcomeAttributionEvent,
    OutcomeAttributionRollup,
    Project,
    ProjectPage,
)
from core.outcome_attribution import (
    backfill_project_outcome_attribution,
    get_project_outcome_attribution_report,
    record_outcome_attribution_event,
)
from core.public_api.views import get_public_project_outcome_attribution


@pytest.mark.django_db
def test_outcome_attribution_pipeline_records_content_distribution_and_technical_events():
    user = User.objects.create_user(username="attrib-user", email="attrib@example.com", password="pass")
    profile = user.profile
    project = Project.objects.create(profile=profile, name="Project", url="https://example.com")

    post = GeneratedBlogPost.objects.create(
        project=project,
        title="Post",
        slug="post",
        tags="seo",
        content="content",
    )

    BlogPostWorkflowAuditLog.objects.create(
        project=project,
        generated_blog_post=post,
        checkpoint="PUBLISH",
        event_type="PUBLISHED",
        actor_profile=profile,
    )

    ProjectPage.objects.create(
        project=project,
        url="https://example.com/docs",
        source="AI",
        type_ai_guess="OTHER",
        date_analyzed=timezone.now(),
    )

    LinkOpportunityAuditLog.objects.create(
        phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
        decision=LinkOpportunityAuditLog.Decision.PLACED,
        source_project=project,
        target_project=project,
        generated_blog_post=post,
        candidate_url="https://example.com/linked",
        candidate_domain="example.com",
        source_domain="example.com",
        link_source="internal",
    )

    names = set(
        OutcomeAttributionEvent.objects.filter(project=project).values_list("event_name", flat=True)
    )
    assert names == {
        "content.blog_post_generated",
        "content.blog_post_published",
        "distribution.link_placement",
        "technical.page_analyzed",
    }


@pytest.mark.django_db
def test_project_outcome_attribution_report_is_project_scoped_and_fast(django_assert_num_queries):
    user = User.objects.create_user(username="attrib-report", email="report@example.com", password="pass")
    project = Project.objects.create(
        profile=user.profile,
        name="Report Project",
        url="https://report.example.com",
    )

    now = timezone.now()

    for index in range(40):
        record_outcome_attribution_event(
            project=project,
            profile=user.profile,
            event_name="content.blog_post_generated",
            source_model="TestSource",
            source_object_id=index,
            occurred_at=now - timedelta(days=index % 7),
            outcome_value=1,
            emit_analytics=False,
        )

    backfill_project_outcome_attribution(project=project)

    start_date = (now - timedelta(days=30)).date()
    end_date = now.date()

    with django_assert_num_queries(2):
        report = get_project_outcome_attribution_report(
            project=project,
            start_date=start_date,
            end_date=end_date,
        )

    assert report["project_id"] == project.id
    assert report["event_count"] >= 40
    assert report["total_value"] >= 40
    assert report["generated_in_ms"] < 1000
    assert any(item["dimension"] == "content" for item in report["dimensions"])


@pytest.mark.django_db
def test_public_outcome_attribution_endpoint_returns_project_report():
    user = User.objects.create_user(username="attrib-api", email="api@example.com", password="pass")
    project = Project.objects.create(profile=user.profile, name="API", url="https://api.example.com")

    OutcomeAttributionRollup.objects.create(
        project=project,
        window_start=timezone.now().date(),
        granularity="DAY",
        dimension="content",
        outcome_metric="blog_posts_generated",
        total_value=3,
        event_count=3,
    )

    request = SimpleNamespace(auth=user.profile)
    response = get_public_project_outcome_attribution(request, project.id, days=7)

    assert response["status"] == "success"
    assert response["project_id"] == project.id
    assert response["event_count"] >= 3
