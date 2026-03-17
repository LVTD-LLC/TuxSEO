from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    BlogPostTitleSuggestion,
    BlogPostWorkflowAuditLog,
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


def _mark_profile_paid(profile):
    profile.user.is_superuser = True
    profile.user.save(update_fields=["is_superuser"])


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
def test_paid_source_blocks_free_external_project_from_promotion(
    monkeypatch, blog_post_with_title_suggestion
):
    blog_post = blog_post_with_title_suggestion
    _mark_profile_paid(blog_post.project.profile)

    free_target_user = User.objects.create_user(username="free-target", password="x")
    free_target_project = Project.objects.create(
        profile=free_target_user.profile,
        url="https://free-target.example.com",
        name="Free target",
        particiate_in_link_exchange=True,
    )
    free_target_page = ProjectPage.objects.create(
        project=free_target_project,
        url="https://free-target.example.com/page",
        title="Free target page",
        description="Free target description",
        summary="Free target summary",
        embedding=_vec((0, 1.0)),
    )

    monkeypatch.setattr("core.models.get_jina_embedding", lambda _text: _vec((0, 1.0)))

    _safe_internal, safe_external = blog_post._evaluate_safe_link_opportunities(
        internal_pages=[],
        external_pages=[free_target_page],
    )

    assert safe_external == []

    suggestion_log = LinkOpportunityAuditLog.objects.get(
        generated_blog_post=blog_post,
        phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
        candidate_page=free_target_page,
    )
    assert suggestion_log.decision == LinkOpportunityAuditLog.Decision.BLOCKED
    assert "target_project_not_paid_for_promotion" in suggestion_log.reasons
    assert "free_project_cannot_be_promoted_into_paid_post" in suggestion_log.reasons


@pytest.mark.django_db
def test_free_source_can_receive_paid_external_link_when_relevant(
    monkeypatch, blog_post_with_title_suggestion
):
    blog_post = blog_post_with_title_suggestion

    paid_target_user = User.objects.create_user(username="paid-target", password="x")
    _mark_profile_paid(paid_target_user.profile)

    paid_target_project = Project.objects.create(
        profile=paid_target_user.profile,
        url="https://paid-target.example.com",
        name="Paid target",
        particiate_in_link_exchange=True,
    )
    paid_target_page = ProjectPage.objects.create(
        project=paid_target_project,
        url="https://paid-target.example.com/page",
        title="Paid target page",
        description="Paid target description",
        summary="Paid target summary",
        embedding=_vec((0, 1.0)),
    )

    monkeypatch.setattr("core.models.get_jina_embedding", lambda _text: _vec((0, 1.0)))

    _safe_internal, safe_external = blog_post._evaluate_safe_link_opportunities(
        internal_pages=[],
        external_pages=[paid_target_page],
    )

    assert [page.id for page in safe_external] == [paid_target_page.id]

    suggestion_log = LinkOpportunityAuditLog.objects.get(
        generated_blog_post=blog_post,
        phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
        candidate_page=paid_target_page,
    )
    assert suggestion_log.decision == LinkOpportunityAuditLog.Decision.ALLOWED
    assert "source_project_free" in suggestion_log.policy_flags
    assert "target_project_paid" in suggestion_log.policy_flags
    assert "eligibility_passed" in suggestion_log.policy_flags
    assert "relevance_passed" in suggestion_log.policy_flags


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


@pytest.mark.django_db
def test_insert_links_blocks_external_candidates_when_approval_pending(monkeypatch, blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion

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

    blog_post.external_links_approval_status = GeneratedBlogPost.ApprovalStatus.PENDING
    blog_post.save(update_fields=["external_links_approval_status"])

    monkeypatch.setattr(
        blog_post,
        "_get_link_candidate_pages",
        lambda max_pages=4, max_external_pages=3: ([], [external_page], []),
    )

    result = blog_post.insert_links_into_post()

    assert result == blog_post.content
    blocked_log = LinkOpportunityAuditLog.objects.get(
        generated_blog_post=blog_post,
        phase=LinkOpportunityAuditLog.Phase.SUGGESTION,
        candidate_page=external_page,
    )
    assert blocked_log.decision == LinkOpportunityAuditLog.Decision.BLOCKED
    assert "awaiting_external_links_approval" in blocked_log.reasons


@pytest.mark.django_db
def test_apply_approval_decision_updates_post_and_creates_workflow_audit(blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion

    blog_post.apply_approval_decision(
        checkpoint="publish",
        decision="approve",
        actor_profile=blog_post.project.profile,
        reason="Looks client-safe",
    )

    blog_post.refresh_from_db()
    assert blog_post.publish_approval_status == GeneratedBlogPost.ApprovalStatus.APPROVED

    workflow_log = BlogPostWorkflowAuditLog.objects.get(generated_blog_post=blog_post)
    assert workflow_log.checkpoint == "PUBLISH"
    assert workflow_log.event_type == "REVIEW_DECISION"
    assert workflow_log.reason == "Looks client-safe"


@pytest.mark.django_db
def test_workflow_audit_log_is_immutable(blog_post_with_title_suggestion):
    blog_post = blog_post_with_title_suggestion
    audit_log = blog_post.create_workflow_audit_event(
        checkpoint="PUBLISH",
        event_type="REVIEW_DECISION",
        actor_profile=blog_post.project.profile,
        decision="APPROVED",
    )

    with pytest.raises(ValueError):
        audit_log.reason = "mutated"
        audit_log.save()
