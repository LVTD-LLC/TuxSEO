# Detail View Analytics Event Spec (Paid modules)

This document defines the canonical telemetry for paid `ProjectPageDetailView` modules.

## Event schema baseline

All events use canonical names from `core/analytics/event_taxonomy.json`.

Common dimensions (when available):

- `project_id`
- `project_page_id`
- `result_status` (`queued`, `succeeded`, `failed`)
- `event_schema_version` and `event_stage` are automatically attached by `core.tasks.track_event`.

## Events

### `detail_view_opened`
When: user loads a paid page detail workspace.

Required properties:
- `project_id`
- `project_page_id`
- `result_status`

Additional recommended properties:
- `seo_state`
- `backlink_state`

### `seo_analysis_run_started`
When: SEO analysis run is accepted and queued.

Required properties:
- `project_id`
- `project_page_id`
- `trigger`
- `result_status`

Additional recommended properties:
- `run_id`

### `seo_analysis_run_completed`
When: background run finishes successfully.

Required properties:
- `project_id`
- `project_page_id`
- `run_id`
- `result_status`

### `seo_analysis_run_failed`
When: background run fails (including feature-flag disable at execution time).

Required properties:
- `project_id`
- `project_page_id`
- `run_id`
- `result_status`

Additional recommended properties:
- `failure_reason`

### `backlink_discovery_started`
When: backlink discovery refresh is accepted and queued.

Required properties:
- `project_id`
- `project_page_id`
- `trigger`
- `result_status`

### `backlink_discovery_completed`
When: backlink discovery task returns successfully.

Required properties:
- `project_id`
- `project_page_id`
- `result_status`

Additional recommended properties:
- `candidates_count`
- `contact_enrichment_enabled`

### `backlink_discovery_failed`
When: backlink discovery task fails or module is disabled at runtime.

Required properties:
- `project_id`
- `project_page_id`
- `result_status`

Additional recommended properties:
- `failure_reason`
- `debug_reason`

### `opportunities_viewed`
When: backlink opportunities are rendered with at least one candidate.

Required properties:
- `project_id`
- `project_page_id`
- `opportunities_count`
- `result_status`

Additional recommended properties:
- `sort_mode`
- `has_contact_only`

### `contact_method_copied`
When: user copies a discovered contact method from page detail UI.

Required properties:
- `project_id`
- `project_page_id`
- `contact_method`
- `result_status`
