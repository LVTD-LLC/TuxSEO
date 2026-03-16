from __future__ import annotations

import hashlib
import time
from datetime import date, datetime

import posthog
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from core.analytics import ANALYTICS_EVENTS, EVENT_TAXONOMY_VERSION
from core.models import OutcomeAttributionEvent, OutcomeAttributionRollup, Project

OUTCOME_ATTRIBUTION_SCHEMA_VERSION = 1
ROLLUP_GRANULARITY_DAY = "DAY"

OUTCOME_ATTRIBUTION_EVENTS = {
    "content.blog_post_generated": {
        "dimension": OutcomeAttributionEvent.Dimension.CONTENT,
        "outcome_metric": "blog_posts_generated",
    },
    "content.blog_post_published": {
        "dimension": OutcomeAttributionEvent.Dimension.CONTENT,
        "outcome_metric": "blog_posts_published",
    },
    "distribution.link_placement": {
        "dimension": OutcomeAttributionEvent.Dimension.DISTRIBUTION,
        "outcome_metric": "links_placed",
    },
    "technical.page_analyzed": {
        "dimension": OutcomeAttributionEvent.Dimension.TECHNICAL,
        "outcome_metric": "pages_analyzed",
    },
}


def _normalize_occurred_at(value: datetime | None) -> datetime:
    if value is None:
        return timezone.now()
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _build_event_fingerprint(
    *,
    project_id: int,
    event_name: str,
    source_model: str,
    source_object_id: int | None,
    occurred_at: datetime,
) -> str:
    raw = f"{project_id}|{event_name}|{source_model}|{source_object_id}|{occurred_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _emit_posthog_event(*, event: OutcomeAttributionEvent) -> None:
    profile = event.profile
    if not settings.POSTHOG_API_KEY or profile is None:
        return

    try:
        posthog.capture(
            profile.user.email,
            event=ANALYTICS_EVENTS.OUTCOME_ATTRIBUTION_RECORDED,
            properties={
                "project_id": event.project_id,
                "attribution_event_name": event.event_name,
                "attribution_dimension": event.dimension,
                "attribution_outcome_metric": event.outcome_metric,
                "attribution_outcome_value": event.outcome_value,
                "outcome_attribution_schema_version": event.schema_version,
                "event_schema_version": EVENT_TAXONOMY_VERSION,
            },
        )
    except Exception:
        # Attribution should never fail the primary workflow.
        return


def _increment_rollup(*, event: OutcomeAttributionEvent) -> None:
    window_start = event.occurred_at.date()
    rollup, created = OutcomeAttributionRollup.objects.get_or_create(
        project=event.project,
        window_start=window_start,
        granularity=ROLLUP_GRANULARITY_DAY,
        dimension=event.dimension,
        outcome_metric=event.outcome_metric,
        defaults={
            "total_value": event.outcome_value,
            "event_count": 1,
            "last_aggregated_at": timezone.now(),
        },
    )
    if created:
        return

    OutcomeAttributionRollup.objects.filter(pk=rollup.pk).update(
        total_value=F("total_value") + event.outcome_value,
        event_count=F("event_count") + 1,
        last_aggregated_at=timezone.now(),
    )


def record_outcome_attribution_event(
    *,
    project: Project,
    event_name: str,
    source_model: str,
    source_object_id: int | None,
    profile=None,
    occurred_at: datetime | None = None,
    outcome_value: float = 1.0,
    metadata: dict | None = None,
    emit_analytics: bool = True,
) -> tuple[OutcomeAttributionEvent, bool]:
    event_definition = OUTCOME_ATTRIBUTION_EVENTS.get(event_name)
    if event_definition is None:
        raise ValueError(f"Unknown outcome attribution event: {event_name}")

    occurred_at = _normalize_occurred_at(occurred_at)
    fingerprint = _build_event_fingerprint(
        project_id=project.id,
        event_name=event_name,
        source_model=source_model,
        source_object_id=source_object_id,
        occurred_at=occurred_at,
    )

    with transaction.atomic():
        event, created = OutcomeAttributionEvent.objects.get_or_create(
            event_fingerprint=fingerprint,
            defaults={
                "project": project,
                "profile": profile or project.profile,
                "event_name": event_name,
                "dimension": event_definition["dimension"],
                "outcome_metric": event_definition["outcome_metric"],
                "outcome_value": outcome_value,
                "source_model": source_model,
                "source_object_id": source_object_id,
                "occurred_at": occurred_at,
                "metadata": metadata or {},
                "schema_version": OUTCOME_ATTRIBUTION_SCHEMA_VERSION,
            },
        )

        if created:
            _increment_rollup(event=event)

    if created and emit_analytics:
        _emit_posthog_event(event=event)

    return event, created


def get_project_outcome_attribution_report(
    *,
    project: Project,
    start_date: date,
    end_date: date,
) -> dict:
    timer_start = time.perf_counter()

    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    rollups = list(
        OutcomeAttributionRollup.objects.filter(
            project=project,
            granularity=ROLLUP_GRANULARITY_DAY,
            window_start__gte=start_date,
            window_start__lte=end_date,
        ).values("dimension", "outcome_metric").annotate(
            total_value=Sum("total_value"),
            event_count=Sum("event_count"),
        )
    )

    by_dimension: dict[str, dict] = {}
    total_value = 0.0
    total_events = 0

    for row in rollups:
        dimension = row["dimension"]
        metric_name = row["outcome_metric"]
        metric_total = float(row["total_value"] or 0.0)
        metric_events = int(row["event_count"] or 0)

        if dimension not in by_dimension:
            by_dimension[dimension] = {
                "dimension": dimension,
                "total_value": 0.0,
                "event_count": 0,
                "metrics": [],
            }

        by_dimension[dimension]["total_value"] += metric_total
        by_dimension[dimension]["event_count"] += metric_events
        by_dimension[dimension]["metrics"].append(
            {
                "metric": metric_name,
                "total_value": metric_total,
                "event_count": metric_events,
            }
        )

        total_value += metric_total
        total_events += metric_events

    top_events = list(
        OutcomeAttributionEvent.objects.filter(
            project=project,
            occurred_at__date__gte=start_date,
            occurred_at__date__lte=end_date,
        )
        .values("event_name", "dimension")
        .annotate(total_value=Sum("outcome_value"), event_count=Count("id"))
        .order_by("-total_value", "-event_count", "event_name")[:10]
    )

    generated_in_ms = int((time.perf_counter() - timer_start) * 1000)

    return {
        "project_id": project.id,
        "schema_version": OUTCOME_ATTRIBUTION_SCHEMA_VERSION,
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "total_value": total_value,
        "event_count": total_events,
        "dimensions": sorted(by_dimension.values(), key=lambda row: row["dimension"]),
        "top_events": [
            {
                "event_name": row["event_name"],
                "dimension": row["dimension"],
                "total_value": float(row["total_value"] or 0.0),
                "event_count": int(row["event_count"] or 0),
            }
            for row in top_events
        ],
        "generated_in_ms": generated_in_ms,
    }


def rebuild_outcome_attribution_rollups(*, project: Project, start_date: date, end_date: date) -> int:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    events = (
        OutcomeAttributionEvent.objects.filter(
            project=project,
            occurred_at__date__gte=start_date,
            occurred_at__date__lte=end_date,
        )
        .values("occurred_at__date", "dimension", "outcome_metric")
        .annotate(total_value=Sum("outcome_value"), event_count=Count("id"))
    )

    OutcomeAttributionRollup.objects.filter(
        project=project,
        granularity=ROLLUP_GRANULARITY_DAY,
        window_start__gte=start_date,
        window_start__lte=end_date,
    ).delete()

    rollups_to_create = [
        OutcomeAttributionRollup(
            project=project,
            window_start=row["occurred_at__date"],
            granularity=ROLLUP_GRANULARITY_DAY,
            dimension=row["dimension"],
            outcome_metric=row["outcome_metric"],
            total_value=float(row["total_value"] or 0.0),
            event_count=int(row["event_count"] or 0),
            last_aggregated_at=timezone.now(),
        )
        for row in events
    ]

    if rollups_to_create:
        OutcomeAttributionRollup.objects.bulk_create(rollups_to_create)

    return len(rollups_to_create)


def backfill_project_outcome_attribution(*, project: Project) -> dict:
    from core.models import BlogPostWorkflowAuditLog, GeneratedBlogPost, LinkOpportunityAuditLog, ProjectPage

    created_events = 0

    for post in GeneratedBlogPost.objects.filter(project=project).only("id", "created_at"):
        _, created = record_outcome_attribution_event(
            project=project,
            profile=project.profile,
            event_name="content.blog_post_generated",
            source_model="GeneratedBlogPost",
            source_object_id=post.id,
            occurred_at=post.created_at,
            metadata={"backfill": True},
            emit_analytics=False,
        )
        created_events += int(created)

    for workflow_event in BlogPostWorkflowAuditLog.objects.filter(
        project=project,
        event_type="PUBLISHED",
    ).only("id", "created_at", "generated_blog_post_id"):
        _, created = record_outcome_attribution_event(
            project=project,
            profile=project.profile,
            event_name="content.blog_post_published",
            source_model="BlogPostWorkflowAuditLog",
            source_object_id=workflow_event.id,
            occurred_at=workflow_event.created_at,
            metadata={
                "backfill": True,
                "generated_blog_post_id": workflow_event.generated_blog_post_id,
            },
            emit_analytics=False,
        )
        created_events += int(created)

    for link_log in LinkOpportunityAuditLog.objects.filter(
        source_project=project,
        phase=LinkOpportunityAuditLog.Phase.PLACEMENT,
        decision=LinkOpportunityAuditLog.Decision.PLACED,
    ).only("id", "created_at"):
        _, created = record_outcome_attribution_event(
            project=project,
            profile=project.profile,
            event_name="distribution.link_placement",
            source_model="LinkOpportunityAuditLog",
            source_object_id=link_log.id,
            occurred_at=link_log.created_at,
            metadata={"backfill": True},
            emit_analytics=False,
        )
        created_events += int(created)

    for page in ProjectPage.objects.filter(project=project, date_analyzed__isnull=False).only(
        "id", "date_analyzed"
    ):
        _, created = record_outcome_attribution_event(
            project=project,
            profile=project.profile,
            event_name="technical.page_analyzed",
            source_model="ProjectPage",
            source_object_id=page.id,
            occurred_at=page.date_analyzed,
            metadata={"backfill": True},
            emit_analytics=False,
        )
        created_events += int(created)

    return {
        "project_id": project.id,
        "created_events": created_events,
    }
