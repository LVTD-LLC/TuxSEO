# Paid acquisition attribution schema (v1)

This document defines the canonical paid-acquisition schema used by TuxSEO server-side events and PostHog analysis.

## Goals

- Preserve **first-touch** and **latest-touch** attribution per account (`Profile`) and project (`Project`).
- Keep event properties comparable across Meta, Google, Reddit, and X campaigns.
- Avoid accidental PII leakage from URL/query parameters.

## Storage model

- `Profile.first_touch_attribution` (JSON)
- `Profile.latest_touch_attribution` (JSON)
- `Project.first_touch_attribution` (JSON)
- `Project.latest_touch_attribution` (JSON)

Session staging key before signup:
- `acquisition_attribution_v1`

`first_touch` is immutable once set (unless manually corrected).
`latest_touch` updates whenever new attribution query params are observed.

## Capture sources

### Landing/session capture

Middleware reads query params and stores normalized attribution payload in session:

- UTM params: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`
- Click IDs (when present): `gclid`, `fbclid`, `ttclid`, `twclid`, `xclid`, `msclkid`, `li_fat_id`, `rdt_cid`
- Campaign context params: `campaign_id`, `campaign_name`, `adset_id`, `adset_name`, `ad_id`, `creative_id`, `creative_key`, `copy_variant`, `variant`, `channel`, `platform`, `offer`, `geo`, `device`

### Conversion capture (server truth)

`track_event(...)` enriches server-side funnel/revenue events with attribution properties from profile/project context.

Project-scoped events use project attribution when `project_id` is present and belongs to the profile.

## Canonical event properties

Latest touch is emitted as top-level canonical fields for easy querying:

- `channel`, `platform`
- `campaign_id`, `campaign_name`
- `adset_id`, `adset_name`
- `ad_id`, `creative_id`, `creative_key`
- `copy_variant`
- `landing_page`, `offer`, `geo`, `device`
- `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`
- click IDs (`gclid`, `fbclid`, ...)

Both touch snapshots are also available with prefixes:

- `first_touch_*`
- `latest_touch_*`

Metadata fields:

- `acquisition_schema_version=1`
- `attribution_scope` (`profile` or `project`)

## Data-quality guardrails

- Values are truncated to 200 chars.
- Empty values are dropped.
- Email-like values are dropped to reduce PII risk.
- Unknown query params are ignored (only allowlisted keys are captured).
- Malformed attribution payloads are dropped from events with warning logs.

## Funnel/revenue event coverage

Server-side funnel/revenue events now include canonical attribution fields:

- `signup_completed`
- `onboarding_completed`
- `project_create_succeeded`
- `first_content_generated`
- `checkout_started`
- `checkout_succeeded`
- `subscription_created`
- `subscription_started`
- `paid_conversion`

## Marketing usage guidelines

Recommended URL params for paid tests:

- required baseline: `utm_source`, `utm_medium`, `utm_campaign`
- experiment keys: `copy_variant`, `creative_key`
- optional ad hierarchy: `campaign_id`, `adset_id`, `ad_id`

Example:

```text
https://tuxseo.com/signup?utm_source=google&utm_medium=cpc&utm_campaign=seo-q2&campaign_id=123&adset_id=456&ad_id=789&copy_variant=A&creative_key=hero-red-v2
```
