# TuxSEO Analytics Event Taxonomy (PostHog)

Canonical analytics event names live in:

- `core/analytics/event_taxonomy.json`

This file is the **single source of truth** for event names, required properties, and deprecated aliases.

## Actor identity strategy

TuxSEO uses a dual identity model so funnels are stable and queryable:

- **PostHog distinct_id**: authenticated user email (`profile.user.email`) from backend capture.
- **Stable actor dimensions** (always attached server-side in `track_event`):
  - `profile_id`
  - `email`
  - `actor_id_type=profile_id`
  - `actor_id=<profile_id>`
  - `plan`
  - `current_state`

For critical outcomes, capture is done server-side (not client-only).

## How code should consume event names

- Backend (Python): `core.analytics.events`
  - `ANALYTICS_EVENTS` for constants
  - `normalize_event_name()` for alias mapping
- Frontend (JS): `frontend/src/constants/analytics_events.js`
  - imports the same JSON and exports matching constants

## Canonical events (v2 highlights)

### P1/P2 funnel + product coverage

- `signup_completed`
- `login_succeeded`
- `project_create_succeeded`
- `integration_connected`
- `integration_disconnected`
- `keyword_updated`
- `page_analysis_completed`
- `title_generation_completed`
- `content_generation_succeeded`
- `content_generation_failed`
- `publish_attempted`
- `publish_succeeded`
- `publish_failed`
- `link_exchange_toggled`
- `plan_upgraded`
- `plan_cancelled`
- `analytics_page_viewed`
- `analytics_date_range_changed`
- `analytics_refresh_clicked`
- `analytics_source_error_shown`

See `event_taxonomy.json` for full list + required properties per event.

## Required-property enforcement

`core.tasks.track_event` now validates required properties from taxonomy before capture.
If required properties are missing, event capture is rejected and logged.

## Change policy

When adding or renaming events:

1. Update `core/analytics/event_taxonomy.json`
2. Add an alias in `deprecated_aliases` if a rename is needed
3. Update docs/tests in the same PR
