#!/usr/bin/env python3
"""Bootstrap/update TuxSEO PostHog dashboards for ops, funnel, and LLM health.

Usage:
  POSTHOG_API_KEY=phx_... python scripts/posthog_dashboard_bootstrap.py --project-id 105300

Notes:
- Uses only stdlib HTTP clients (no extra dependencies).
- Idempotent: re-running updates dashboards/insights by name.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


DEFAULT_HOST = "https://us.posthog.com"


@dataclass(frozen=True)
class InsightSpec:
    name: str
    filters: dict[str, Any]


@dataclass(frozen=True)
class DashboardSpec:
    name: str
    description: str
    tags: tuple[str, ...]
    insights: tuple[InsightSpec, ...]


def dashboard_specs() -> tuple[DashboardSpec, ...]:
    return (
        DashboardSpec(
            name="TuxSEO – Operational Health (Logs + Failures)",
            description=(
                "Operational pulse for backend reliability: ingestion heartbeat, "
                "failure trends, and publish error hotspots. "
                "Use dashboard date range + property filters (project_id, email/profile_id)."
            ),
            tags=("tuxseo", "ops", "posthog"),
            insights=(
                InsightSpec(
                    name="Ingestion heartbeat: key event volume (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "events": [
                            {"id": "signup_completed", "type": "events", "order": 0, "math": "total"},
                            {"id": "project_create_succeeded", "type": "events", "order": 1, "math": "total"},
                            {"id": "content_generation_succeeded", "type": "events", "order": 2, "math": "total"},
                            {"id": "publish_succeeded", "type": "events", "order": 3, "math": "total"},
                            {"id": "$ai_generation", "type": "events", "order": 4, "math": "total"},
                        ],
                    },
                ),
                InsightSpec(
                    name="Top operational failure events (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "events": [
                            {"id": "content_generation_failed", "type": "events", "order": 0, "math": "total"},
                            {"id": "publish_failed", "type": "events", "order": 1, "math": "total"},
                            {"id": "abuse_guardrail_triggered", "type": "events", "order": 2, "math": "total"},
                        ],
                    },
                ),
                InsightSpec(
                    name="Publish attempts vs outcomes (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "events": [
                            {"id": "publish_attempted", "type": "events", "order": 0, "math": "total"},
                            {"id": "publish_succeeded", "type": "events", "order": 1, "math": "total"},
                            {"id": "publish_failed", "type": "events", "order": 2, "math": "total"},
                        ],
                    },
                ),
            ),
        ),
        DashboardSpec(
            name="TuxSEO – Product Funnel Health",
            description=(
                "Activation → generation → publish funnel health. "
                "Use dashboard date range + property filters (project_id, email/profile_id)."
            ),
            tags=("tuxseo", "product", "funnel", "posthog"),
            insights=(
                InsightSpec(
                    name="Funnel: signup → project create → content generate → publish",
                    filters={
                        "insight": "FUNNELS",
                        "date_from": "-30d",
                        "layout": "horizontal",
                        "events": [
                            {"id": "signup_completed", "type": "events", "order": 0},
                            {"id": "project_create_succeeded", "type": "events", "order": 1},
                            {"id": "content_generation_succeeded", "type": "events", "order": 2},
                            {"id": "publish_succeeded", "type": "events", "order": 3},
                        ],
                    },
                ),
                InsightSpec(
                    name="Funnel stage throughput (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "events": [
                            {"id": "signup_completed", "type": "events", "order": 0, "math": "total"},
                            {"id": "project_create_succeeded", "type": "events", "order": 1, "math": "total"},
                            {"id": "content_generation_succeeded", "type": "events", "order": 2, "math": "total"},
                            {"id": "publish_succeeded", "type": "events", "order": 3, "math": "total"},
                        ],
                    },
                ),
                InsightSpec(
                    name="Generation failures vs successes (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "events": [
                            {"id": "content_generation_succeeded", "type": "events", "order": 0, "math": "total"},
                            {"id": "content_generation_failed", "type": "events", "order": 1, "math": "total"},
                        ],
                    },
                ),
            ),
        ),
        DashboardSpec(
            name="TuxSEO – LLM Analytics Health",
            description=(
                "LLM reliability/cost performance from $ai_generation events. "
                "Use dashboard date range + property filters (project_id, email/profile_id, feature_path)."
            ),
            tags=("tuxseo", "llm", "posthog"),
            insights=(
                InsightSpec(
                    name="LLM runs by model (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "breakdown": "$ai_model",
                        "breakdown_type": "event",
                        "events": [
                            {"id": "$ai_generation", "type": "events", "order": 0, "math": "total"}
                        ],
                    },
                ),
                InsightSpec(
                    name="LLM failures by model/provider (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "breakdown": "$ai_model",
                        "breakdown_type": "event",
                        "properties": [
                            {
                                "key": "result_status",
                                "type": "event",
                                "operator": "exact",
                                "value": ["failed"],
                            }
                        ],
                        "events": [
                            {"id": "$ai_generation", "type": "events", "order": 0, "math": "total"}
                        ],
                    },
                ),
                InsightSpec(
                    name="LLM average latency in seconds (daily)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "breakdown": "$ai_model",
                        "breakdown_type": "event",
                        "events": [
                            {
                                "id": "$ai_generation",
                                "type": "events",
                                "order": 0,
                                "math": "avg",
                                "math_property": "$ai_latency",
                            }
                        ],
                    },
                ),
                InsightSpec(
                    name="LLM token trend (sum of $ai_total_tokens)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "breakdown": "$ai_model",
                        "breakdown_type": "event",
                        "events": [
                            {
                                "id": "$ai_generation",
                                "type": "events",
                                "order": 0,
                                "math": "sum",
                                "math_property": "$ai_total_tokens",
                            }
                        ],
                    },
                ),
                InsightSpec(
                    name="LLM estimated cost trend (sum of $ai_total_cost_usd)",
                    filters={
                        "insight": "TRENDS",
                        "display": "ActionsLineGraph",
                        "interval": "day",
                        "date_from": "-30d",
                        "breakdown": "$ai_model",
                        "breakdown_type": "event",
                        "events": [
                            {
                                "id": "$ai_generation",
                                "type": "events",
                                "order": 0,
                                "math": "sum",
                                "math_property": "$ai_total_cost_usd",
                            }
                        ],
                    },
                ),
            ),
        ),
    )


class PostHogClient:
    def __init__(self, *, host: str, project_id: str, api_key: str):
        self.host = host.rstrip("/")
        self.project_id = str(project_id)
        self.api_key = api_key

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = f"{self.host}{path}"
        if params:
            return f"{base}?{parse.urlencode(params)}"
        return base

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._url(path, params=params),
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method=method,
        )

        for attempt in range(5):
            try:
                with request.urlopen(req, timeout=60) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except error.HTTPError as exc:
                if exc.code == 429 and attempt < 4:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2 ** attempt
                    time.sleep(delay)
                    continue
                detail = exc.read().decode("utf-8", "ignore")
                raise RuntimeError(f"PostHog API {method} {path} failed ({exc.code}): {detail}") from exc

        raise RuntimeError(f"PostHog API {method} {path} failed after retries")

    def list_dashboards(self) -> list[dict[str, Any]]:
        data = self.request("GET", f"/api/projects/{self.project_id}/dashboards/", params={"limit": 100})
        return [d for d in data.get("results", []) if not d.get("deleted")]

    def create_dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/api/projects/{self.project_id}/dashboards/", payload)

    def update_dashboard(self, dashboard_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/api/projects/{self.project_id}/dashboards/{dashboard_id}/", payload)

    def get_dashboard(self, dashboard_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/projects/{self.project_id}/dashboards/{dashboard_id}/")

    def create_insight(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/api/projects/{self.project_id}/insights/", payload)

    def update_insight(self, insight_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/api/projects/{self.project_id}/insights/{insight_id}/", payload)


def upsert_dashboard(client: PostHogClient, spec: DashboardSpec) -> tuple[dict[str, Any], bool]:
    existing = next((d for d in client.list_dashboards() if d["name"] == spec.name), None)

    payload = {
        "name": spec.name,
        "description": spec.description,
        "pinned": True,
        "is_shared": True,
        "tags": list(spec.tags),
    }

    if existing:
        dashboard = client.update_dashboard(existing["id"], payload)
        return dashboard, False

    dashboard = client.create_dashboard(payload)
    return dashboard, True


def upsert_insights(client: PostHogClient, *, dashboard_id: int, specs: tuple[InsightSpec, ...]) -> tuple[int, int]:
    dashboard = client.get_dashboard(dashboard_id)
    existing_by_name: dict[str, int] = {}
    for tile in dashboard.get("tiles", []):
        insight = tile.get("insight") or {}
        if insight.get("name"):
            existing_by_name[insight["name"]] = insight["id"]

    created = 0
    updated = 0

    for insight_spec in specs:
        payload = {
            "name": insight_spec.name,
            "dashboards": [dashboard_id],
            "filters": insight_spec.filters,
        }

        insight_id = existing_by_name.get(insight_spec.name)
        if insight_id:
            client.update_insight(insight_id, payload)
            updated += 1
        else:
            client.create_insight(payload)
            created += 1

    return created, updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/update TuxSEO PostHog dashboards")
    parser.add_argument("--host", default=os.getenv("POSTHOG_HOST", DEFAULT_HOST))
    parser.add_argument("--project-id", default=os.getenv("POSTHOG_PROJECT_ID", "105300"))
    parser.add_argument("--api-key", default=os.getenv("POSTHOG_API_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        print("Missing POSTHOG_API_KEY", file=sys.stderr)
        return 1

    client = PostHogClient(host=args.host, project_id=args.project_id, api_key=args.api_key)

    summary: list[dict[str, Any]] = []
    for spec in dashboard_specs():
        dashboard, was_created = upsert_dashboard(client, spec)
        created, updated = upsert_insights(client, dashboard_id=dashboard["id"], specs=spec.insights)
        summary.append(
            {
                "dashboard_id": dashboard["id"],
                "name": dashboard["name"],
                "created": was_created,
                "insights_created": created,
                "insights_updated": updated,
                "url": f"{args.host.rstrip('/')}/project/{args.project_id}/dashboard/{dashboard['id']}",
            }
        )

    print(json.dumps({"project_id": str(args.project_id), "dashboards": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
