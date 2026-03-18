import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Profile, ProjectPage, ProjectPageAnalysisRun
from core.seo_analysis import analyze_project_page_seo

RERUN_COOLDOWN_SECONDS = 30
MAX_HISTORY_ITEMS = 5


@dataclass(frozen=True)
class RunStartResult:
    run: ProjectPageAnalysisRun
    created: bool
    reason: str


def _build_compact_analysis_payload(project_page: ProjectPage) -> dict:
    analysis = analyze_project_page_seo(project_page)
    checks = analysis.get("checks") or []

    compact_checks = [
        {
            "label": check.get("label", ""),
            "status": check.get("status", ""),
            "value": check.get("value", ""),
            "why_it_matters": check.get("why_it_matters", ""),
            "how_to_fix": check.get("how_to_fix", ""),
        }
        for check in checks
    ]

    json_ld = analysis.get("json_ld") or {}

    return {
        "score": analysis.get("score", 0),
        "passed_checks": analysis.get("passed_checks", 0),
        "warned_checks": analysis.get("warned_checks", 0),
        "failed_checks": analysis.get("failed_checks", 0),
        "total_checks": analysis.get("total_checks", 0),
        "checks": compact_checks,
        "json_ld": {
            "status_label": json_ld.get("status_label", "Not evaluated"),
            "detected_summary": json_ld.get("detected_summary", "Not evaluated"),
            "detected_types": json_ld.get("detected_types", []),
            "issue_list": json_ld.get("issue_list", []),
            "notes": json_ld.get("notes", []),
            "starter_suggestion": json_ld.get("starter_suggestion"),
        },
        "stored_at": timezone.now().isoformat(),
        "payload_version": 1,
    }


def start_or_reuse_run(
    *,
    project_page: ProjectPage,
    requested_by: Profile | None,
    trigger: str = ProjectPageAnalysisRun.Trigger.MANUAL,
    cooldown_seconds: int = RERUN_COOLDOWN_SECONDS,
) -> RunStartResult:
    now = timezone.now()

    with transaction.atomic():
        locked_page = ProjectPage.objects.select_for_update().get(pk=project_page.pk)
        active_run = (
            ProjectPageAnalysisRun.objects.select_for_update()
            .filter(
                project_page=locked_page,
                status__in=[
                    ProjectPageAnalysisRun.Status.QUEUED,
                    ProjectPageAnalysisRun.Status.RUNNING,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        if active_run:
            return RunStartResult(run=active_run, created=False, reason="active_lock")

        if cooldown_seconds > 0:
            latest_finished_run = (
                ProjectPageAnalysisRun.objects.filter(
                    project_page=locked_page,
                    status=ProjectPageAnalysisRun.Status.SUCCEEDED,
                )
                .exclude(finished_at__isnull=True)
                .order_by("-finished_at")
                .first()
            )
            if latest_finished_run and latest_finished_run.finished_at:
                elapsed = now - latest_finished_run.finished_at
                if elapsed < timedelta(seconds=cooldown_seconds):
                    return RunStartResult(
                        run=latest_finished_run,
                        created=False,
                        reason="cooldown",
                    )

        try:
            run = ProjectPageAnalysisRun.objects.create(
                project_page=locked_page,
                project=locked_page.project,
                requested_by=requested_by,
                trigger=trigger,
                status=ProjectPageAnalysisRun.Status.QUEUED,
                queued_at=now,
            )
        except IntegrityError:
            existing = (
                ProjectPageAnalysisRun.objects.filter(
                    project_page=locked_page,
                    status__in=[
                        ProjectPageAnalysisRun.Status.QUEUED,
                        ProjectPageAnalysisRun.Status.RUNNING,
                    ],
                )
                .order_by("-created_at")
                .first()
            )
            if existing:
                return RunStartResult(run=existing, created=False, reason="active_lock")
            raise

    return RunStartResult(run=run, created=True, reason="created")


def execute_run(*, run: ProjectPageAnalysisRun) -> ProjectPageAnalysisRun:
    with transaction.atomic():
        locked_run = (
            ProjectPageAnalysisRun.objects.select_for_update()
            .select_related("project_page")
            .get(pk=run.pk)
        )
        if locked_run.status != ProjectPageAnalysisRun.Status.QUEUED:
            return locked_run

        locked_run.status = ProjectPageAnalysisRun.Status.RUNNING
        locked_run.started_at = timezone.now()
        locked_run.failure_message = ""
        locked_run.failure_details = {}
        locked_run.save(
            update_fields=[
                "status",
                "started_at",
                "failure_message",
                "failure_details",
                "updated_at",
            ]
        )

    run = locked_run

    try:
        has_content = run.project_page.get_page_content()
        if not has_content:
            raise RuntimeError("Failed to fetch page content for SEO analysis.")

        analyzed = run.project_page.analyze_content()
        if not analyzed:
            raise RuntimeError("Failed to analyze page content.")

        payload = _build_compact_analysis_payload(run.project_page)
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        run.status = ProjectPageAnalysisRun.Status.SUCCEEDED
        run.finished_at = timezone.now()
        run.analysis_payload = payload
        run.payload_checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        run.payload_bytes = len(payload_json.encode("utf-8"))
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "analysis_payload",
                "payload_checksum",
                "payload_bytes",
                "updated_at",
            ]
        )
    except Exception as exc:
        run.status = ProjectPageAnalysisRun.Status.FAILED
        run.finished_at = timezone.now()
        run.failure_message = str(exc)
        run.failure_details = {
            "exception_type": exc.__class__.__name__,
            "message": str(exc),
        }
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "failure_message",
                "failure_details",
                "updated_at",
            ]
        )

    return run


def get_latest_and_history(
    *,
    project_page: ProjectPage,
    history_limit: int = MAX_HISTORY_ITEMS,
) -> tuple:
    latest_run = project_page.get_latest_analysis_run()
    history = list(
        project_page.analysis_runs.order_by("-created_at")[:history_limit].values(
            "id",
            "status",
            "trigger",
            "created_at",
            "started_at",
            "finished_at",
            "payload_bytes",
            "failure_message",
        )
    )
    return latest_run, history
