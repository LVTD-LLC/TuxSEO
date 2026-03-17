import hashlib
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    AnalyticsFactDaily,
    AnalyticsSourceSnapshot,
    AnalyticsSyncCursor,
    Project,
    ProjectIntegration,
)
from tuxseo.utils import get_tuxseo_logger

logger = get_tuxseo_logger(__name__)


class AnalyticsSyncError(Exception):
    pass


class ProviderRateLimitError(AnalyticsSyncError):
    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderAPIError(AnalyticsSyncError):
    pass


@dataclass
class CanonicalFactRow:
    metric_date: date
    dimension_scope: str
    page_url: str = ""
    search_query: str = ""
    country_code: str = ""
    device_type: str = ""
    channel_group: str = ""
    clicks: int | None = None
    impressions: int | None = None
    ctr: Decimal | None = None
    avg_position: Decimal | None = None
    sessions: int | None = None
    users: int | None = None
    engaged_sessions: int | None = None
    bounce_rate: Decimal | None = None
    conversions: Decimal | None = None
    conversion_rate: Decimal | None = None
    provider_payload_meta: dict[str, Any] | None = None


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_ratio(value: Any, *, divide_percent: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None

    try:
        ratio = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AnalyticsSyncError(f"Invalid ratio value: {value}") from exc

    if divide_percent:
        ratio = ratio / Decimal("100")

    if ratio < 0 or ratio > 1:
        raise AnalyticsSyncError(f"Ratio out of range [0,1]: {value}")

    return ratio


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _sanitize_error(message: str, max_len: int = 400) -> str:
    sanitized = (message or "").replace("\n", " ").strip()
    return sanitized[:max_len]


def _request_with_backoff(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 30,
    max_attempts: int = 4,
) -> requests.Response:
    headers = headers or {}

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise ProviderAPIError(f"HTTP request failed: {exc}") from exc
            sleep_seconds = min(2 ** attempt, 20)
            time.sleep(sleep_seconds)
            continue

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After", "")
            retry_after = int(retry_after_header) if retry_after_header.isdigit() else None
            if attempt == max_attempts:
                raise ProviderRateLimitError(
                    f"Rate limited by provider: {url}", retry_after_seconds=retry_after
                )
            time.sleep(retry_after or min(2 ** attempt, 20))
            continue

        if response.status_code >= 500:
            if attempt == max_attempts:
                raise ProviderAPIError(
                    f"Provider server error {response.status_code} for {url}"
                )
            time.sleep(min(2 ** attempt, 20))
            continue

        if response.status_code >= 400:
            body = response.text[:300]
            raise ProviderAPIError(f"Provider error {response.status_code}: {body}")

        return response

    raise ProviderAPIError("Unexpected request retry flow")


def _project_domain(project: Project) -> str:
    parsed = urlparse(project.url)
    return parsed.netloc.lower().replace("www.", "")


def _ga4_source_account_ref(integration: ProjectIntegration) -> str | None:
    # We do not store property ids yet. Try extracting from scope first.
    scope = integration.scope or ""
    for token in scope.split():
        if token.startswith("properties/"):
            return token.split("/", 1)[1]
    return None


def _discover_ga4_property_id(access_token: str, project: Project) -> str | None:
    response = _request_with_backoff(
        method="GET",
        url="https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"pageSize": 200},
        timeout=30,
    )
    payload = response.json()

    domain = _project_domain(project)
    candidates: list[str] = []
    for account in payload.get("accountSummaries", []):
        for prop in account.get("propertySummaries", []):
            prop_name = prop.get("property", "")
            display_name = (prop.get("displayName") or "").lower()
            if prop_name.startswith("properties/"):
                property_id = prop_name.split("/", 1)[1]
                candidates.append(property_id)
                if domain and domain in display_name:
                    return property_id

    return candidates[0] if candidates else None


def _refresh_google_access_token(integration: ProjectIntegration) -> str:
    if not integration.refresh_token:
        return integration.access_token

    now = timezone.now()
    expires_soon = not integration.token_expires_at or integration.token_expires_at <= now + timedelta(
        minutes=5
    )
    if integration.access_token and not expires_soon:
        return integration.access_token

    if not getattr(settings, "GOOGLE_CLIENT_ID", "") or not getattr(
        settings, "GOOGLE_CLIENT_SECRET", ""
    ):
        return integration.access_token

    response = _request_with_backoff(
        method="POST",
        url="https://oauth2.googleapis.com/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": integration.refresh_token,
        },
        timeout=20,
    )
    payload = response.json()

    access_token = payload.get("access_token", "")
    expires_in = payload.get("expires_in")
    if access_token:
        integration.access_token = access_token
        if expires_in:
            integration.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        integration.save(update_fields=["access_token", "token_expires_at", "updated_at"])

    return integration.access_token


def _fetch_ga4_rows(
    *, integration: ProjectIntegration, project: Project, start_date: date, end_date: date
) -> tuple[list[CanonicalFactRow], dict[str, Any], str]:
    access_token = _refresh_google_access_token(integration)
    if not access_token:
        raise ProviderAPIError("Google Analytics integration is missing access token")

    source_account_ref = _ga4_source_account_ref(integration)
    if not source_account_ref:
        source_account_ref = _discover_ga4_property_id(access_token, project)

    if not source_account_ref:
        raise ProviderAPIError(
            "GA4 property id is missing. Reconnect integration with GA4 property access."
        )

    endpoint = f"https://analyticsdata.googleapis.com/v1beta/properties/{source_account_ref}:runReport"
    body = {
        "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
        "dimensions": [{"name": "date"}, {"name": "pagePath"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "engagedSessions"},
            {"name": "bounceRate"},
            {"name": "conversions"},
            {"name": "sessionConversionRate"},
        ],
        "keepEmptyRows": False,
        "limit": 100000,
    }

    response = _request_with_backoff(
        method="POST",
        url=endpoint,
        headers={"Authorization": f"Bearer {access_token}"},
        json=body,
        timeout=60,
    )
    payload = response.json()

    rows: list[CanonicalFactRow] = []
    base = project.url.rstrip("/")

    for row in payload.get("rows", []):
        dim_values = [entry.get("value", "") for entry in row.get("dimensionValues", [])]
        metric_values = [entry.get("value", "") for entry in row.get("metricValues", [])]
        if len(dim_values) < 2:
            continue

        date_value = timezone.datetime.strptime(dim_values[0], "%Y%m%d").date()
        page_path = dim_values[1] or "/"
        page_url = f"{base}{page_path}" if page_path.startswith("/") else f"{base}/{page_path}"

        rows.append(
            CanonicalFactRow(
                metric_date=date_value,
                dimension_scope=AnalyticsFactDaily.DimensionScope.PAGE,
                page_url=page_url[:1024],
                sessions=_to_int(metric_values[0] if len(metric_values) > 0 else None),
                users=_to_int(metric_values[1] if len(metric_values) > 1 else None),
                engaged_sessions=_to_int(metric_values[2] if len(metric_values) > 2 else None),
                bounce_rate=_normalize_ratio(metric_values[3] if len(metric_values) > 3 else None),
                conversions=_to_decimal(metric_values[4] if len(metric_values) > 4 else None),
                conversion_rate=_normalize_ratio(metric_values[5] if len(metric_values) > 5 else None),
            )
        )

    return rows, payload, source_account_ref


def _fetch_gsc_rows(
    *, integration: ProjectIntegration, project: Project, start_date: date, end_date: date
) -> tuple[list[CanonicalFactRow], dict[str, Any], str]:
    access_token = _refresh_google_access_token(integration)
    if not access_token:
        raise ProviderAPIError("Google Search Console integration is missing access token")

    source_account_ref = f"sc-domain:{_project_domain(project)}"
    endpoint = (
        f"https://www.googleapis.com/webmasters/v3/sites/{source_account_ref}/searchAnalytics/query"
    )

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["date", "page", "query", "country", "device"],
        "rowLimit": 25000,
        "startRow": 0,
    }

    rows: list[CanonicalFactRow] = []
    payload_rows: list[dict[str, Any]] = []
    while True:
        response = _request_with_backoff(
            method="POST",
            url=endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
            timeout=60,
        )
        payload = response.json()
        fetched_rows = payload.get("rows", [])
        if not fetched_rows:
            break

        payload_rows.extend(fetched_rows)

        for item in fetched_rows:
            keys = item.get("keys", [])
            if len(keys) < 5:
                continue
            metric_date = timezone.datetime.strptime(keys[0], "%Y-%m-%d").date()
            page = (keys[1] or "")[:1024]
            query = (keys[2] or "")[:512]
            country = (keys[3] or "").upper()[:2]
            device = (keys[4] or "").lower()[:32]

            dimension_scope = AnalyticsFactDaily.DimensionScope.SITE
            if page and query:
                dimension_scope = AnalyticsFactDaily.DimensionScope.PAGE_QUERY
            elif query:
                dimension_scope = AnalyticsFactDaily.DimensionScope.QUERY
            elif page:
                dimension_scope = AnalyticsFactDaily.DimensionScope.PAGE
            elif country:
                dimension_scope = AnalyticsFactDaily.DimensionScope.COUNTRY
            elif device:
                dimension_scope = AnalyticsFactDaily.DimensionScope.DEVICE

            rows.append(
                CanonicalFactRow(
                    metric_date=metric_date,
                    dimension_scope=dimension_scope,
                    page_url=page,
                    search_query=query,
                    country_code=country,
                    device_type=device,
                    clicks=_to_int(item.get("clicks")),
                    impressions=_to_int(item.get("impressions")),
                    ctr=_normalize_ratio(item.get("ctr")),
                    avg_position=_to_decimal(item.get("position")),
                )
            )

        if len(fetched_rows) < body["rowLimit"]:
            break
        body["startRow"] += body["rowLimit"]

    payload_json = {
        "rows": payload_rows,
        "request": {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": body["dimensions"],
        },
    }
    return rows, payload_json, source_account_ref


def _fetch_plausible_rows(
    *, integration: ProjectIntegration, start_date: date, end_date: date
) -> tuple[list[CanonicalFactRow], dict[str, Any], str]:
    if not integration.plausible_api_key or not integration.plausible_site_id:
        raise ProviderAPIError("Plausible integration missing API key or site id")

    source_account_ref = integration.plausible_site_id
    base_url = (integration.plausible_base_url or "https://plausible.io").rstrip("/")

    endpoint = f"{base_url}/api/v2/query"
    body = {
        "site_id": integration.plausible_site_id,
        "date_range": [start_date.isoformat(), end_date.isoformat()],
        "metrics": ["visitors", "visits", "bounce_rate"],
        "dimensions": ["time:day", "event:page"],
    }

    response = _request_with_backoff(
        method="POST",
        url=endpoint,
        headers={"Authorization": f"Bearer {integration.plausible_api_key}"},
        json=body,
        timeout=45,
    )

    payload = response.json()
    rows: list[CanonicalFactRow] = []

    def _parse_metric_date(item: dict[str, Any]) -> date | None:
        if item.get("date"):
            return timezone.datetime.strptime(item["date"], "%Y-%m-%d").date()
        dimensions = item.get("dimensions") or []
        if dimensions:
            candidate = str(dimensions[0])
            try:
                return timezone.datetime.strptime(candidate[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        if item.get("time:day"):
            try:
                return timezone.datetime.strptime(str(item["time:day"])[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    for item in payload.get("results", []):
        metric_date = _parse_metric_date(item)
        if not metric_date:
            continue

        page_path = item.get("event:page") or item.get("page") or item.get("name")
        if not page_path:
            dimensions = item.get("dimensions") or []
            if len(dimensions) > 1:
                page_path = dimensions[1]
        page_path = page_path or "/"

        metrics = item.get("metrics") or []
        visitors = item.get("visitors") if item.get("visitors") is not None else (
            metrics[0] if len(metrics) > 0 else None
        )
        visits = item.get("visits") if item.get("visits") is not None else (
            metrics[1] if len(metrics) > 1 else None
        )
        bounce_rate = item.get("bounce_rate") if item.get("bounce_rate") is not None else (
            metrics[2] if len(metrics) > 2 else None
        )

        page_url = (
            page_path if str(page_path).startswith("http") else f"https://{source_account_ref}{page_path}"
        )

        divide_percent = False
        if bounce_rate not in (None, ""):
            try:
                divide_percent = Decimal(str(bounce_rate)) > 1
            except (InvalidOperation, TypeError, ValueError):
                divide_percent = False

        rows.append(
            CanonicalFactRow(
                metric_date=metric_date,
                dimension_scope=AnalyticsFactDaily.DimensionScope.PAGE,
                page_url=str(page_url)[:1024],
                sessions=_to_int(visits),
                users=_to_int(visitors),
                bounce_rate=_normalize_ratio(bounce_rate, divide_percent=divide_percent),
            )
        )

    return rows, payload, source_account_ref


def _determine_sync_window(cursor: AnalyticsSyncCursor | None, lookback_days: int = 2) -> tuple[date, date]:
    today = timezone.now().date()
    end_date = today

    if cursor and cursor.last_successful_date:
        start_date = min(today, cursor.last_successful_date - timedelta(days=lookback_days) + timedelta(days=1))
    else:
        start_date = today - timedelta(days=90)

    if start_date > end_date:
        start_date = end_date

    return start_date, end_date


def _snapshot_fingerprint(provider: str, source_ref: str, start_date: date, end_date: date) -> str:
    return _hash_key(f"{provider}:{source_ref}:{start_date.isoformat()}:{end_date.isoformat()}")


def _dimension_fingerprint(row: CanonicalFactRow) -> str:
    parts = [
        row.dimension_scope or "",
        row.page_url or "",
        row.search_query or "",
        row.country_code or "",
        row.device_type or "",
        row.channel_group or "",
    ]
    return _hash_key("|".join(parts))


def _save_source_snapshot(
    *,
    project: Project,
    integration: ProjectIntegration,
    provider: str,
    source_account_ref: str,
    start_date: date,
    end_date: date,
    payload_json: dict[str, Any],
    rows_count: int,
    status: str,
    error_code: str = "",
    error_message: str = "",
) -> AnalyticsSourceSnapshot:
    return AnalyticsSourceSnapshot.objects.create(
        project=project,
        integration=integration,
        provider=provider,
        source_account_ref=source_account_ref,
        request_fingerprint=_snapshot_fingerprint(provider, source_account_ref, start_date, end_date),
        window_start_date=start_date,
        window_end_date=end_date,
        payload_json=payload_json,
        rows_count=rows_count,
        fetched_at=timezone.now(),
        status=status,
        error_code=error_code,
        error_message=error_message,
    )


def _upsert_canonical_rows(
    *,
    project: Project,
    provider: str,
    snapshot: AnalyticsSourceSnapshot,
    rows: list[CanonicalFactRow],
    batch_size: int = 1000,
) -> int:
    if not rows:
        return 0

    now = timezone.now()
    objects: list[AnalyticsFactDaily] = []
    for row in rows:
        page_url = (row.page_url or "")[:1024]
        search_query = (row.search_query or "")[:512]
        objects.append(
            AnalyticsFactDaily(
                project=project,
                provider=provider,
                metric_date=row.metric_date,
                dimension_scope=row.dimension_scope,
                page_url=page_url,
                page_url_key=_hash_key(page_url) if page_url else "",
                search_query=search_query,
                search_query_key=_hash_key(search_query) if search_query else "",
                country_code=(row.country_code or "")[:2],
                device_type=(row.device_type or "")[:32],
                channel_group=(row.channel_group or "")[:64],
                dimension_fingerprint=_dimension_fingerprint(row),
                clicks=row.clicks,
                impressions=row.impressions,
                ctr=row.ctr,
                avg_position=row.avg_position,
                sessions=row.sessions,
                users=row.users,
                engaged_sessions=row.engaged_sessions,
                bounce_rate=row.bounce_rate,
                conversions=row.conversions,
                conversion_rate=row.conversion_rate,
                provider_payload_meta=row.provider_payload_meta or {},
                source_snapshot=snapshot,
                ingested_at=now,
            )
        )

    upserted = 0
    for index in range(0, len(objects), batch_size):
        chunk = objects[index : index + batch_size]
        AnalyticsFactDaily.objects.bulk_create(
            chunk,
            batch_size=batch_size,
            update_conflicts=True,
            unique_fields=[
                "project",
                "provider",
                "metric_date",
                "dimension_scope",
                "dimension_fingerprint",
            ],
            update_fields=[
                "page_url",
                "page_url_key",
                "search_query",
                "search_query_key",
                "country_code",
                "device_type",
                "channel_group",
                "clicks",
                "impressions",
                "ctr",
                "avg_position",
                "sessions",
                "users",
                "engaged_sessions",
                "bounce_rate",
                "conversions",
                "conversion_rate",
                "provider_payload_meta",
                "source_snapshot",
                "ingested_at",
                "updated_at",
            ],
        )
        upserted += len(chunk)

    return upserted


def sync_project_provider_analytics(project_id: int, provider: str) -> dict[str, Any]:
    project = Project.objects.get(id=project_id)

    integration = (
        ProjectIntegration.objects.filter(project=project, provider=provider)
        .order_by("id")
        .first()
    )
    if not integration or not integration.is_connected:
        logger.info(
            "[AnalyticsSync] Skipping disconnected integration",
            project_id=project.id,
            provider=provider,
        )
        return {"status": "skipped", "provider": provider, "reason": "integration_missing_or_disconnected"}

    cursor = (
        AnalyticsSyncCursor.objects.filter(project=project, provider=provider)
        .order_by("-updated_at")
        .first()
    )
    if not cursor:
        try:
            cursor, _ = AnalyticsSyncCursor.objects.get_or_create(
                project=project,
                provider=provider,
                source_account_ref="pending",
                defaults={"last_status": AnalyticsSyncCursor.SyncStatus.PENDING},
            )
        except IntegrityError:
            cursor = (
                AnalyticsSyncCursor.objects.filter(project=project, provider=provider)
                .order_by("-updated_at")
                .first()
            )

    start_date, end_date = _determine_sync_window(cursor)
    cursor.last_run_started_at = timezone.now()
    cursor.last_status = AnalyticsSyncCursor.SyncStatus.RUNNING
    cursor.save(update_fields=["last_run_started_at", "last_status", "updated_at"])

    try:
        if provider == ProjectIntegration.Provider.GOOGLE_ANALYTICS:
            rows, payload_json, source_account_ref = _fetch_ga4_rows(
                integration=integration,
                project=project,
                start_date=start_date,
                end_date=end_date,
            )
            provider_value = AnalyticsFactDaily.Provider.GA4
        elif provider == ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE:
            rows, payload_json, source_account_ref = _fetch_gsc_rows(
                integration=integration,
                project=project,
                start_date=start_date,
                end_date=end_date,
            )
            provider_value = AnalyticsFactDaily.Provider.GSC
        elif provider == ProjectIntegration.Provider.PLAUSIBLE:
            rows, payload_json, source_account_ref = _fetch_plausible_rows(
                integration=integration,
                start_date=start_date,
                end_date=end_date,
            )
            provider_value = AnalyticsFactDaily.Provider.PLAUSIBLE
        else:
            raise AnalyticsSyncError(f"Unsupported provider: {provider}")

        with transaction.atomic():
            cursor.source_account_ref = source_account_ref
            snapshot = _save_source_snapshot(
                project=project,
                integration=integration,
                provider=provider_value,
                source_account_ref=source_account_ref,
                start_date=start_date,
                end_date=end_date,
                payload_json=payload_json,
                rows_count=len(rows),
                status=AnalyticsSourceSnapshot.FetchStatus.SUCCESS,
            )
            upserted = _upsert_canonical_rows(
                project=project,
                provider=provider_value,
                snapshot=snapshot,
                rows=rows,
            )

            cursor.last_successful_date = end_date
            cursor.last_run_finished_at = timezone.now()
            cursor.last_status = AnalyticsSyncCursor.SyncStatus.SUCCESS
            cursor.last_error = ""
            cursor.save(
                update_fields=[
                    "source_account_ref",
                    "last_successful_date",
                    "last_run_finished_at",
                    "last_status",
                    "last_error",
                    "updated_at",
                ]
            )

        return {
            "status": "success",
            "provider": provider,
            "rows_fetched": len(rows),
            "rows_upserted": upserted,
            "window": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        }

    except Exception as exc:
        error_message = _sanitize_error(str(exc))
        logger.exception(
            "[AnalyticsSync] Provider sync failed",
            project_id=project.id,
            provider=provider,
            error=error_message,
        )

        status = AnalyticsSyncCursor.SyncStatus.FAILED
        error_code = "sync_error"
        if isinstance(exc, ProviderRateLimitError):
            status = AnalyticsSyncCursor.SyncStatus.PARTIAL
            error_code = "rate_limited"

        source_account_ref = cursor.source_account_ref if cursor.source_account_ref != "pending" else "unknown"

        _save_source_snapshot(
            project=project,
            integration=integration,
            provider=(
                AnalyticsFactDaily.Provider.GA4
                if provider == ProjectIntegration.Provider.GOOGLE_ANALYTICS
                else AnalyticsFactDaily.Provider.GSC
                if provider == ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE
                else AnalyticsFactDaily.Provider.PLAUSIBLE
            ),
            source_account_ref=source_account_ref,
            start_date=start_date,
            end_date=end_date,
            payload_json={},
            rows_count=0,
            status=AnalyticsSourceSnapshot.FetchStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

        cursor.last_run_finished_at = timezone.now()
        cursor.last_status = status
        cursor.last_error = error_message
        cursor.save(update_fields=["last_run_finished_at", "last_status", "last_error", "updated_at"])

        return {
            "status": "failed",
            "provider": provider,
            "error": error_message,
            "window": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        }


def schedule_all_connected_project_analytics_syncs() -> dict[str, Any]:
    from django_q.tasks import async_task

    scheduled = 0
    providers_scheduled: list[str] = []

    connected_integrations = ProjectIntegration.objects.filter(
        status=ProjectIntegration.Status.CONNECTED,
        provider__in=[
            ProjectIntegration.Provider.GOOGLE_ANALYTICS,
            ProjectIntegration.Provider.GOOGLE_SEARCH_CONSOLE,
            ProjectIntegration.Provider.PLAUSIBLE,
        ],
        project__deleted_at__isnull=True,
    ).select_related("project")

    running_cutoff = timezone.now() - timedelta(hours=2)

    for integration in connected_integrations:
        running_cursor_exists = AnalyticsSyncCursor.objects.filter(
            project_id=integration.project_id,
            provider=integration.provider,
            last_status=AnalyticsSyncCursor.SyncStatus.RUNNING,
            last_run_started_at__gte=running_cutoff,
        ).exists()
        if running_cursor_exists:
            logger.info(
                "[AnalyticsSync] Skip scheduling duplicate running task",
                project_id=integration.project_id,
                provider=integration.provider,
            )
            continue

        async_task(
            "core.tasks.sync_project_integration_analytics",
            integration.project_id,
            integration.provider,
            group="Analytics Sync",
        )
        scheduled += 1
        providers_scheduled.append(f"{integration.project_id}:{integration.provider}")

    logger.info(
        "[AnalyticsSync] Scheduled connected project provider jobs",
        total_scheduled=scheduled,
        jobs=providers_scheduled,
    )

    return {"scheduled": scheduled, "jobs": providers_scheduled}
