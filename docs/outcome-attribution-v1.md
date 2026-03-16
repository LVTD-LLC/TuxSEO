# Outcome Attribution v1

## Goal

Answer at project level: **"what contributed to this result?"**

v1 tracks contribution events across three minimum dimensions:

- `content`
- `distribution` (links/distribution)
- `technical` (technical operations)

## Data model

### `OutcomeAttributionEvent`

Immutable fact table linking an execution action to an outcome metric.

Key fields:

- `project`, `profile`
- `event_name` (stable schema key)
- `dimension` (`content|distribution|technical`)
- `outcome_metric` (e.g. `blog_posts_generated`, `links_placed`)
- `outcome_value` (numeric contribution value, default `1.0`)
- `occurred_at`
- `source_model`, `source_object_id`
- `event_fingerprint` (dedupe/idempotency)
- `schema_version`
- `metadata`

### `OutcomeAttributionRollup`

Pre-aggregated daily primitive for dashboard windows.

Key fields:

- `project`
- `window_start` (day)
- `granularity` (`DAY` in v1)
- `dimension`
- `outcome_metric`
- `total_value`
- `event_count`
- `last_aggregated_at`

## Event schema (v1)

Stable event keys emitted by pipeline:

- `content.blog_post_generated` → metric `blog_posts_generated`
- `content.blog_post_published` → metric `blog_posts_published`
- `distribution.link_placement` → metric `links_placed`
- `technical.page_analyzed` → metric `pages_analyzed`

## Pipeline wiring

Automatic capture via Django signals:

- `GeneratedBlogPost` create → `content.blog_post_generated`
- `BlogPostWorkflowAuditLog` with `event_type=PUBLISHED` → `content.blog_post_published`
- `LinkOpportunityAuditLog` with `phase=PLACEMENT` + `decision=PLACED` → `distribution.link_placement`
- `ProjectPage` transition to `date_analyzed != null` → `technical.page_analyzed`

Each created event also increments daily rollup rows.

## Analytics stack compatibility

When configured with PostHog, each attribution event emits canonical analytics event:

- `outcome_attribution_recorded`

Properties include project id, attribution dimension, attribution metric, value, and schema versions.

## Backfill strategy

`backfill_project_outcome_attribution(project=...)` reconstructs v1 events from existing records:

- historical generated blog posts
- historical publish workflow events
- historical placed link logs
- historical analyzed project pages

This is idempotent via `event_fingerprint`.

## Reporting primitive

`get_project_outcome_attribution_report(project, start_date, end_date)` returns:

- window totals
- dimension-level totals and metric breakdowns
- top event contributors
- report generation latency (`generated_in_ms`)

Public API endpoint:

- `GET /public-api/projects/{project_id}/outcome-attribution?days=30`
