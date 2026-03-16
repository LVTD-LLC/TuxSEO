from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    BlogPostTitleSuggestion,
    GeneratedBlogPost,
    LinkOpportunityAuditLog,
    Project,
    ProjectPage,
)


def _vec(*pairs):
    data = [0.0] * 1024
    for idx, value in pairs:
        data[idx] = value
    return data


@pytest.fixture
def blog_post_with_title_suggestion(db):
    user = User.objects.create_user(username="safe-links", password="x")
    source_project = Project.objects.create(
        profile=user.profile,
        url="https://source.example.com",
        name="Source",
        particiate_in_link_exchange=True,
    )
    suggestion = BlogPostTitleSuggestion.objects.create(
        project=source_project,
        title="Safe links",
        description="desc",
        suggested_meta_description="safe links for seo",
    )
    blog_post = GeneratedBlogPost.objects.create(
        project=source_project,
        title_suggestion=suggestion,
        title="Post",
        description="desc",
        slug="post",
        tags="seo",
        content="Body content",
    )
    return blog_post


@pytest.mark.django_db
def test_external_link_blocked_when_opt_in_or_relevance_fails(monkeypatch, blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion

    blog_post.project.particiate_in_link_exchange = False
    blog_post.project.save(update_fields=["particiate_in_link_exchange"])

    target_project = Project.objects.create(
        profile=blog_post.project.profile,
        url="https://target.example.com",
        name="Target",
        particiate_in_link_exchange=True,
    )
    target_page = ProjectPage.objects.create(
        project=target_project,
        url="https://target.example.com/page",
        title="Target page",
        description="Target description",
        summary="Target summary",
        embedding=_vec((0, 1.0)),
    )

    monkeypatch.setattr("core.models.get_jina_embedding", lambda _text: _vec((1, 1.0)))

    _safe_internal, safe_external = blog_post._evaluate_safe_link_opportunities(
        internal_pages=[],
        external_pages=[target_page],
    )

    assert safe_external == []

    suggestion_log = LinkOpportunityAuditLog.objects.get(
        generated_blog_post=blog_post,
        phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
        candidate_page=target_page,
    )
    assert suggestion_log.decision == LinkOpportunityAuditLog.Decision.BLOCKED
    assert "source_project_not_opted_in" in suggestion_log.reasons
    assert "below_relevance_threshold" in suggestion_log.reasons


@pytest.mark.django_db
def test_velocity_and_anchor_diversity_caps_block_candidate(monkeypatch, blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion

    target_project = Project.objects.create(
        profile=blog_post.project.profile,
        url="https://target.example.com",
        name="Target",
        particiate_in_link_exchange=True,
    )
    target_page = ProjectPage.objects.create(
        project=target_project,
        url="https://target.example.com/page",
        title="Target page",
        description="Target description",
        summary="Target summary",
        embedding=_vec((0, 1.0)),
    )

    for _ in range(3):
        log = LinkOpportunityAuditLog.objects.create(
            phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
            decision=LinkOpportunityAuditLog.Decision.PLACED,
            source_project=blog_post.project,
            target_project=target_project,
            generated_blog_post=blog_post,
            candidate_page=target_page,
            candidate_url=target_page.url,
            candidate_domain="target.example.com",
            source_domain="source.example.com",
            link_source="external",
            final_anchor="target page",
        )
        LinkOpportunityAuditLog.objects.filter(id=log.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )

    monkeypatch.setattr("core.models.get_jina_embedding", lambda _text: _vec((0, 1.0)))

    _safe_internal, safe_external = blog_post._evaluate_safe_link_opportunities(
        internal_pages=[],
        external_pages=[target_page],
    )

    assert safe_external == []

    suggestion_log = LinkOpportunityAuditLog.objects.filter(
        generated_blog_post=blog_post,
        phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
        candidate_page=target_page,
    ).latest("id")

    assert suggestion_log.decision == LinkOpportunityAuditLog.Decision.BLOCKED
    assert "domain_velocity_cap_exceeded" in suggestion_log.reasons
    assert "source_target_velocity_cap_exceeded" in suggestion_log.reasons
    assert "anchor_diversity_cap_exceeded" in suggestion_log.reasons


@pytest.mark.django_db
def test_record_link_placement_audit_logs_tracks_placed_and_not_placed(blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion

    internal_page = ProjectPage.objects.create(
        project=blog_post.project,
        url="https://source.example.com/guide",
        title="Guide",
        description="Guide",
        summary="Guide",
    )
    external_project = Project.objects.create(
        profile=blog_post.project.profile,
        url="https://external.example.com",
        name="External",
        particiate_in_link_exchange=True,
    )
    external_page = ProjectPage.objects.create(
        project=external_project,
        url="https://external.example.com/page",
        title="External Page",
        description="External Page",
        summary="External Page",
    )

    blog_post._record_link_placement_audit_logs(
        candidate_pages=[internal_page, external_page],
        content_with_links="Use [guide](https://source.example.com/guide).",
    )

    placement_logs = list(
        LinkOpportunityAuditLog.objects.filter(
            generated_blog_post=blog_post,
            phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
        ).order_by("candidate_url")
    )

    assert len(placement_logs) == 2
    placed = next(log for log in placement_logs if log.candidate_url == internal_page.url)
    not_placed = next(log for log in placement_logs if log.candidate_url == external_page.url)

    assert placed.decision == LinkOpportunityAuditLog.Decision.PLACED
    assert placed.final_anchor == "guide"
    assert not_placed.decision == LinkOpportunityAuditLog.Decision.NOT_PLACED
    assert "agent_did_not_place_link" in not_placed.reasons
