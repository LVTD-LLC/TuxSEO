# Analytics page v1: QA data-correctness checklist + production troubleshooting

## Scope

This checklist validates the v1 Analytics page (`/project/<id>/analytics/`) end-to-end:

- API payload correctness (`/api/projects/<id>/analytics/aggregation`)
- UI rendering correctness (KPIs, source health, trend, breakdowns)
- telemetry reliability for interaction/error signals:
  - `analytics_page_viewed`
  - `analytics_date_range_changed`
  - `analytics_refresh_clicked`
  - `analytics_source_error_shown`

## QA checklist (pre-ship + regression)

### 1) Access and baseline shell

- [ ] Logged-out user is redirected to login.
- [ ] Logged-in user cannot open another user's project analytics page (404).
- [ ] Owner sees all expected sections: Overview KPIs, Sessions trend, Source state, Source breakdown, Top pages.

### 2) Date range controls + query behavior

- [ ] Default load uses Last 30d and a valid inclusive date window.
- [ ] Last 7d and Last 90d presets update both date inputs correctly.
- [ ] Custom start/end + Refresh returns data for the exact selected range.
- [ ] Validation error appears when either start/end is missing.

### 3) Data correctness cross-checks

Run API and compare to rendered UI values for the same date range:

```bash
curl -sS "http://localhost:8000/api/projects/<PROJECT_ID>/analytics/aggregation?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD" \
  -H "Cookie: sessionid=<SESSION>"
```

- [ ] KPI totals match API `overview` (`clicks`, `impressions`, `sessions`, `users`, `conversions`).
- [ ] CTR and conversion rate match API percent fields (2 decimal places).
- [ ] Source breakdown table rows map 1:1 to API `source_breakdown` rows.
- [ ] Trend bars presence/empty-state follows API `daily_trend` content.
- [ ] Top pages table matches API `page_breakdown` ordering and values.

### 4) Partial/missing integration behavior

- [ ] Missing integration shows `Missing` badge and explanatory copy.
- [ ] Connected but stale integration shows `Stale` badge.
- [ ] Connected with healthy sync metadata shows `Connected` badge.
- [ ] Page does not crash when only one provider has data.

### 5) Telemetry correctness (PostHog)

Open PostHog Live Events (project 105300) and verify properties are attached.

- [ ] Page load emits `analytics_page_viewed` once with: `project_id`, `date_range_start`, `date_range_end`, `range_days`.
- [ ] Preset switch emits `analytics_date_range_changed` with `change_source=preset_click`.
- [ ] Custom date change + Refresh emits `analytics_date_range_changed` with `change_source=custom_date_refresh`.
- [ ] Refresh click emits `analytics_refresh_clicked`.
- [ ] API/source error surfaced in UI emits `analytics_source_error_shown` with `source`, `error_message`, `result_status=shown`.

## Production troubleshooting notes

### Symptom: Analytics page appears empty

1. Check source health card:
   - `Missing` => integration not connected.
   - `Stale` + error detail => provider sync issue.
2. Inspect latest sync cursor rows for the project in admin (`AnalyticsSyncCursor`).
3. Confirm ingestion snapshots exist (`AnalyticsSourceSnapshot`) and are recent.
4. Verify date range isn't excluding known data window.

### Symptom: KPI mismatch vs expected provider dashboard

1. Call aggregation API directly for same date range and compare to UI.
2. Confirm canonical metric ownership assumptions:
   - clicks/impressions from search scope
   - sessions/users/conversions from traffic scope
3. Check for stale provider cursor and last sync errors.
4. Validate timezone/date-boundary assumptions (inclusive day counts).

### Symptom: Repeated error toasts/messages

1. Inspect browser network call to aggregation endpoint and response status.
2. Use PostHog event `analytics_source_error_shown` to identify:
   - `source` (`dashboard_api` or provider from source health)
   - `source_status`
   - `error_message`
3. If provider errors repeat, run/inspect provider sync task logs and retry after fixing upstream credentials/rate limits.

## Operational note

When changing Analytics page event names or required properties, update in the same PR:

1. `core/analytics/event_taxonomy.json`
2. `frontend/src/controllers/analytics_dashboard_controller.js`
3. event docs (`docs/posthog-event-coverage-matrix.md`, this checklist)
4. test coverage for taxonomy and controller references
