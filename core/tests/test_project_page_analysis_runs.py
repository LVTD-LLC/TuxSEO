import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import Project, ProjectPage, ProjectPageAnalysisRun
from core.project_page_analysis_runs import execute_run, start_or_reuse_run


@pytest.mark.django_db
def test_execute_run_transitions_to_succeeded_and_stores_payload(monkeypatch):
    user = User.objects.create_user(
        "analysis-run-success",
        "analysis-run-success@example.com",
        "secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        title="Sample title",
        description="Sample description",
        markdown_content="# Sample",
        summary="Sample summary",
        date_analyzed=timezone.now(),
        type_ai_guess="product page",
    )

    monkeypatch.setattr(ProjectPage, "get_page_content", lambda _self: True)
    monkeypatch.setattr(ProjectPage, "analyze_content", lambda _self: True)

    from core import project_page_analysis_runs as runs_module

    monkeypatch.setattr(
        runs_module,
        "analyze_project_page_seo",
        lambda _page: {
            "score": 88,
            "passed_checks": 4,
            "warned_checks": 1,
            "failed_checks": 0,
            "total_checks": 5,
            "checks": [
                {
                    "label": "Title length",
                    "status": "pass",
                    "value": "52 chars",
                    "why_it_matters": "Improves CTR",
                    "how_to_fix": "No action needed",
                }
            ],
            "json_ld": {
                "status_label": "Looks good",
                "detected_summary": "WebPage",
                "detected_types": ["WebPage"],
                "issue_list": [],
                "notes": ["valid"],
                "starter_suggestion": None,
            },
        },
    )

    start_result = start_or_reuse_run(project_page=page, requested_by=user.profile)
    assert start_result.created is True
    assert start_result.run.status == ProjectPageAnalysisRun.Status.QUEUED

    completed = execute_run(run=start_result.run)
    completed.refresh_from_db()

    assert completed.status == ProjectPageAnalysisRun.Status.SUCCEEDED
    assert completed.started_at is not None
    assert completed.finished_at is not None
    assert completed.analysis_payload["score"] == 88
    assert completed.payload_checksum
    assert completed.payload_bytes > 0


@pytest.mark.django_db
def test_start_or_reuse_run_dedupes_when_active_run_exists():
    user = User.objects.create_user(
        "analysis-run-dedupe",
        "analysis-run-dedupe@example.com",
        "secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        type_ai_guess="product page",
    )

    active_run = ProjectPageAnalysisRun.objects.create(
        project=project,
        project_page=page,
        requested_by=user.profile,
        status=ProjectPageAnalysisRun.Status.RUNNING,
    )

    result = start_or_reuse_run(project_page=page, requested_by=user.profile)

    assert result.created is False
    assert result.reason == "active_lock"
    assert result.run.id == active_run.id


@pytest.mark.django_db
def test_start_or_reuse_run_enforces_cooldown():
    user = User.objects.create_user(
        "analysis-run-cooldown",
        "analysis-run-cooldown@example.com",
        "secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        type_ai_guess="product page",
    )

    finished_run = ProjectPageAnalysisRun.objects.create(
        project=project,
        project_page=page,
        requested_by=user.profile,
        status=ProjectPageAnalysisRun.Status.SUCCEEDED,
        finished_at=timezone.now(),
    )

    result = start_or_reuse_run(
        project_page=page,
        requested_by=user.profile,
        cooldown_seconds=60,
    )

    assert result.created is False
    assert result.reason == "cooldown"
    assert result.run.id == finished_run.id


@pytest.mark.django_db
def test_execute_run_transitions_to_failed_and_captures_error(monkeypatch):
    user = User.objects.create_user(
        "analysis-run-fail",
        "analysis-run-fail@example.com",
        "secret",
    )
    project = Project.objects.create(
        profile=user.profile,
        url="https://example.com",
        name="Example",
    )
    page = ProjectPage.objects.create(
        project=project,
        url="https://example.com/page",
        type_ai_guess="product page",
    )

    monkeypatch.setattr(ProjectPage, "get_page_content", lambda _self: False)

    start_result = start_or_reuse_run(project_page=page, requested_by=user.profile)
    completed = execute_run(run=start_result.run)
    completed.refresh_from_db()

    assert completed.status == ProjectPageAnalysisRun.Status.FAILED
    assert completed.finished_at is not None
    assert "Failed to fetch page content" in completed.failure_message
    assert completed.failure_details["exception_type"] == "RuntimeError"
