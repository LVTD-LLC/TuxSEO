# Integration Analytics Architecture v1 (GA4 + GSC + Plausible)

## Status
Proposed (implementation-ready)

## Problem
TuxSEO has integration auth/connectivity for Google Analytics 4 (GA4), Google Search Console (GSC), and Plausible, but no finalized storage contract for analytics ingestion and reporting.

Without a stable data model, each provider would push ad-hoc fields into product features, making cross-provider SEO reporting brittle and expensive to evolve.

## Goals (v1)
1. Define one provider-agnostic storage shape that supports GA4, GSC, and Plausible.
2. Keep ingestion safe and idempotent.
3. Support SEO-first reporting for the first release with limited scope.
4. Keep the model extensible so adding a new provider does not require major schema changes.

## Non-goals (v1)
- Real-time streaming ingestion.
- Full event-level warehousing for GA4/Plausible.
- Every possible provider metric/dimension.
- Attribution modeling beyond source data normalization.

---

## Architecture

### High-level pipeline
1. **Connector layer** (provider-specific fetchers)
   - Pulls data from GA4 Data API, GSC Search Analytics API, Plausible Stats API.
   - Writes provider payload snapshots to raw storage.
2. **Normalization layer**
   - Maps provider rows into a canonical metric fact table.
   - Preserves provider-specific dimensions/metadata in JSON when no canonical field exists.
3. **Serving layer**
   - Product queries hit normalized daily fact data by project/date/dimension.
   - Optional pre-aggregated materializations can be added later without changing ingestion contracts.

### Storage tiers
- **Tier A: Raw ingestion snapshots** (audit/replay/debug)
- **Tier B: Canonical normalized facts** (app reporting)
- **Tier C: Optional aggregates/materializations** (performance optimization only)

---

## Data model

### 1) `analytics_source_snapshot` (raw tier)
**Purpose:** preserve provider responses with replay capability.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| project_id | FK | TuxSEO project |
| integration_id | FK nullable | linked integration record |
| provider | enum | `ga4`, `gsc`, `plausible` |
| source_account_ref | varchar(255) | e.g., GA4 property id, GSC site URL, Plausible site id |
| request_fingerprint | char(64) | hash of logical query params |
| window_start_date | date | inclusive |
| window_end_date | date | inclusive |
| payload_json | jsonb | full provider payload (possibly compressed at storage layer) |
| rows_count | integer | extracted row count |
| fetched_at | timestamptz | fetch completion timestamp |
| status | enum | `success`, `partial`, `failed` |
| error_code | varchar(64) nullable | provider/API error class |
| error_message | text nullable | truncated/sanitized |
| created_at | timestamptz | |

**Indexes**
- `(project_id, provider, window_start_date, window_end_date, fetched_at desc)`
- `(request_fingerprint)`

**Retention**
- Keep **90 days** raw snapshots.
- Allow replay from snapshots during that window.

---

### 2) `analytics_fact_daily` (normalized tier)
**Purpose:** provider-agnostic daily metric fact table for SEO reporting.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| project_id | FK | |
| provider | enum | `ga4`, `gsc`, `plausible` |
| metric_date | date | grain = 1 day |
| dimension_scope | enum | `site`, `page`, `query`, `page_query`, `country`, `device`, `channel` |
| page_url | varchar(1024) nullable | normalized canonical URL (hard-capped for index safety) |
| page_url_key | char(64) nullable | sha256 of normalized `page_url` |
| search_query | varchar(512) nullable | query term (primarily GSC, hard-capped for index safety) |
| search_query_key | char(64) nullable | sha256 of normalized `search_query` |
| country_code | char(2) nullable | ISO-3166-1 alpha-2 |
| device_type | varchar(32) nullable | `desktop`, `mobile`, `tablet`, `other` |
| channel_group | varchar(64) nullable | normalized acquisition channel (when available) |
| clicks | bigint nullable | |
| impressions | bigint nullable | |
| ctr | numeric(10,6) nullable | normalized ratio in range [0,1] |
| avg_position | numeric(8,3) nullable | GSC position |
| sessions | bigint nullable | |
| users | bigint nullable | |
| engaged_sessions | bigint nullable | GA4/Plausible when available |
| bounce_rate | numeric(10,6) nullable | normalized ratio in range [0,1] |
| conversions | numeric(18,6) nullable | numeric for fractional modeled conversions |
| conversion_rate | numeric(10,6) nullable | normalized ratio in range [0,1] |
| provider_payload_meta | jsonb nullable | unmapped provider dimensions/flags |
| source_snapshot_id | UUID FK nullable | lineage to raw snapshot |
| ingested_at | timestamptz | |
| updated_at | timestamptz | |

**Uniqueness / idempotency key**
Unique on:
`(project_id, provider, metric_date, dimension_scope, coalesce(page_url_key,''), coalesce(search_query_key,''), coalesce(country_code,''), coalesce(device_type,''), coalesce(channel_group,''))`

**Indexes**
- `(project_id, metric_date desc)`
- `(project_id, provider, metric_date desc)`
- `(project_id, dimension_scope, metric_date desc)`
- Partial indexes for `page_url_key is not null` and `search_query_key is not null`

**Retention**
- Keep normalized facts **indefinitely** (or min 24 months if cost cap is required).

---

### 3) `analytics_sync_cursor` (operational tier)
**Purpose:** reliable incremental syncing and backfill tracking.

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| project_id | FK | |
| provider | enum | `ga4`, `gsc`, `plausible` |
| source_account_ref | varchar(255) | provider account/property/site identifier |
| last_successful_date | date nullable | inclusive end-date synced |
| backfill_start_date | date nullable | first date pending backfill |
| backfill_end_date | date nullable | backfill target end |
| last_run_started_at | timestamptz nullable | |
| last_run_finished_at | timestamptz nullable | |
| last_status | enum | `success`, `partial`, `failed` |
| last_error | text nullable | sanitized |
| updated_at | timestamptz | |

Unique on `(project_id, provider, source_account_ref)`. This prevents cursor collisions when one project connects multiple accounts/properties for the same provider.

---

## Raw vs normalized strategy by provider

### GA4
**Raw**
- Store full API response page(s) per fetch window in `analytics_source_snapshot.payload_json`.
- Include request dimensions/metrics and pagination token lineage.

**Normalized (v1)**
- Dimensions: `date`, `pagePath(+querystring normalized to URL)`, `country`, `deviceCategory`, `sessionDefaultChannelGroup`.
- Metrics: `sessions`, `totalUsers`, `engagedSessions`, `bounceRate`, `conversions`, `sessionConversionRate`.
- Scope mapping:
  - site-level summary
  - page-level daily
  - country/device/channel daily where enabled

### GSC
**Raw**
- Store full Search Analytics response with request config (`type=web`, rowLimit, dimensions used).

**Normalized (v1)**
- Dimensions: `date`, `page`, `query`, `country`, `device`.
- Metrics: `clicks`, `impressions`, `ctr`, `position` (as `avg_position`).
- Scope mapping:
  - site
  - page
  - query
  - page_query (when provider row has both `page` + `query` dimensions)
  - country
  - device
- Deterministic scope rule: use the most specific matching scope from this order:
  `page_query` > `query` > `page` > `country` > `device` > `site`.

### Plausible
**Raw**
- Store original query payload and response (timeseries/breakdown rows).

**Normalized (v1)**
- Dimensions: `date`, `page`, `country`, `device`, `channel` (when available from source/medium breakdown).
- Metrics: `visitors`→`users`, `visits`→`sessions`, `bounce_rate`, `conversion_rate`, optional goal completions→`conversions`.
- Scope mapping:
  - site
  - page
  - country/device/channel

---

## Canonical SEO metrics and dimensions for first pass

### Dimensions (v1)
- `metric_date`
- `page_url`
- `search_query` (GSC only initially)
- `country_code`
- `device_type`
- `channel_group`
- `provider`

### Metrics (v1)
- Search performance: `clicks`, `impressions`, `ctr`, `avg_position`
- Traffic/engagement: `sessions`, `users`, `engaged_sessions`, `bounce_rate`
- Conversion signals: `conversions`, `conversion_rate`

### Ratio normalization contract (mandatory)
- `ctr`, `bounce_rate`, and `conversion_rate` are always stored as decimal ratios in `[0,1]`.
- Provider percentages (e.g., Plausible `bounce_rate=54.3`) must be divided by 100 before insert.
- Values outside `[0,1]` must fail mapping validation and be recorded as sync errors (not partially written).

### Explicitly deferred
- Hourly grain
- Event/action-level custom dimensions
- Multi-touch attribution joins
- Keyword clustering at ingest time

---

## Refresh cadence and performance constraints

### Cadence
- **Daily sync job per provider/project** (default every 6h, with 2-day rolling lookback).
- **Backfill on connect**: last 90 days (provider limits permitting), chunked in 7-day windows.
- **Late-arriving correction**: always re-sync `today-2` through `today`.

### Performance guardrails
- API pull window chunking (7 days default; 1 day for high-cardinality query pulls).
- Hard row caps per run (configurable, e.g., 50k normalized rows/provider/project/run).
- Upsert in batches (e.g., 1k rows) with transaction boundaries to avoid lock bloat.
- Circuit-breaker on repeated provider failures; cursor records partial status.

### Expected query patterns
- Last 30/90 day trend by project/provider.
- Top pages and queries by clicks/impressions/sessions.
- Country/device/channel breakouts.

For these patterns, daily fact table + targeted indexes should keep p95 app queries under ~500ms for typical project sizes.

---

## Extensibility contract (new providers)
To add a new provider without major refactor:
1. Implement provider fetcher -> write `analytics_source_snapshot`.
2. Implement provider mapper -> emit canonical rows into `analytics_fact_daily`.
3. Store provider-only fields in `provider_payload_meta` until promoted to first-class columns.
4. Reuse existing sync cursor and scheduling primitives keyed by `(project_id, provider, source_account_ref)`.

No schema change required unless a new metric is promoted to canonical.

---

## Reliability and safety rules
- All ingestion writes must be idempotent via unique key upsert.
- Mapper enforces `page_url` and `search_query` caps (1024/512) and populates stable hash keys used by uniqueness constraints.
- Never delete normalized facts in regular sync; prefer overwrite/upsert by key/date.
- Log provider errors with sanitized messages (no secrets/tokens).
- Keep lineage (`source_snapshot_id`) for debugging trust issues.
- If one provider fails, others continue (isolated failures).

---

## Implementation checklist
1. Add Django models + migrations for:
   - `analytics_source_snapshot`
   - `analytics_fact_daily`
   - `analytics_sync_cursor`
2. Build provider adapters (GA4, GSC, Plausible) returning canonical row DTOs.
3. Create ingestion service:
   - fetch -> persist raw -> normalize -> batched upsert facts -> update cursor
4. Add Celery tasks per provider/project and scheduling.
5. Add tests:
   - mapper unit tests per provider
   - upsert/idempotency tests
   - cursor progression + partial failure behavior
6. Expose read API/service queries for dashboard widgets.

---

## Acceptance criteria (for implementation)
- Spec covers **GA4 + GSC + Plausible** explicitly.
- Raw vs normalized storage boundaries are defined.
- Canonical daily fact schema and idempotency key are defined.
- Refresh cadence, retention, and performance guardrails are defined.
- Extensibility path for new providers is clear and does not require major refactor.
