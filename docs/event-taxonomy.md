# TuxSEO Analytics Event Taxonomy

Canonical analytics event names live in:

- `core/analytics/event_taxonomy.json`

This file is the **single source of truth** for event names and deprecated aliases.

## How code should consume event names

- Backend (Python): `core.analytics.events`
  - `ANALYTICS_EVENTS` for constants
  - `normalize_event_name()` for alias mapping
- Frontend (JS): `frontend/src/constants/analytics_events.js`
  - imports the same JSON file and exports equivalent constants

## Canonical events (v1)

- `site_visited`
- `signup_started`
- `signup_completed`
- `email_verified`
- `project_create_succeeded`
- `project_deleted`
- `first_title_generated`
- `first_blog_generated`
- `first_publish_attempt`
- `pricing_cta_clicked`
- `checkout_started`
- `checkout_succeeded`
- `checkout_failed`
- `subscription_created`
- `subscription_upgraded`
- `subscription_cancelled`
- `subscription_deleted`
- `outcome_attribution_recorded`

## Deprecated aliases

- `user_signed_up` → `signup_completed`
- `project_created` → `project_create_succeeded`
- `first_post_generated` → `first_blog_generated`
- `checkout_completed` → `checkout_succeeded`

`track_event` normalizes deprecated names before sending events to PostHog.

## Change policy

When adding or renaming events:

1. Update `core/analytics/event_taxonomy.json`
2. Add an alias in `deprecated_aliases` if a rename is needed
3. Update docs/tests in the same PR
