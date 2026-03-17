# PostHog dashboard pack (Ops + Funnel + LLM + Paid attribution)

This document defines and links the first-pass TuxSEO dashboard set requested for operational + product + LLM analytics visibility.

## Live dashboards (project `105300`)

- **Operational health (logs + failures)**  
  https://us.posthog.com/project/105300/dashboard/1371380
- **Product funnel health**  
  https://us.posthog.com/project/105300/dashboard/1371381
- **LLM analytics health**  
  https://us.posthog.com/project/105300/dashboard/1371382
- **Paid acquisition attribution**  
  https://us.posthog.com/project/105300/dashboard/1371507

> These dashboards are managed by code (script below). Re-run the script to safely update/repair tiles. Dashboards stay private to project members (not public-link shared).

## What each dashboard answers

### 1) Operational health (Logs + failures)
Primary question: **"What is broken right now?"**

Tiles:
1. `Ingestion heartbeat: key event volume (daily)`
   - Detects event ingestion drops for key milestones (`signup_completed`, `project_create_succeeded`, `content_generation_succeeded`, `publish_succeeded`, `$ai_generation`).
2. `Top operational failure events (daily)`
   - Tracks `content_generation_failed`, `publish_failed`, and `abuse_guardrail_triggered`.
3. `Publish attempts vs outcomes (daily)`
   - Compares attempted/succeeded/failed publish outcomes.

### 2) Product funnel health
Primary question: **"Where do users drop off?"**

Tiles:
1. `Funnel: signup → project create → content generate → publish`
2. `Funnel stage throughput (daily)`
3. `Generation failures vs successes (daily)`

### 3) LLM analytics health
Primary question: **"Which model paths are expensive/slow/fragile?"**

Tiles:
1. `LLM runs by model (daily)`
2. `LLM failures by model/provider (daily)`
3. `LLM average latency in seconds (daily)`
4. `LLM token trend (sum of $ai_total_tokens)`
5. `LLM estimated cost trend (sum of $ai_total_cost_usd)`

### 4) Paid acquisition attribution
Primary question: **"Which paid channel/campaign/creative/copy variant converts best?"**

Tiles:
1. `Channel performance: paid conversions by channel (daily)`
2. `Campaign/adset/ad performance (paid_conversion)`
3. `Copy/creative variant performance (paid_conversion)`
4. `Time to paid conversion by channel`

## Filtering conventions (project/account/time)

For all three dashboards:

- Use PostHog **global date range** in the dashboard header (e.g. last 7/30/90 days).
- Add property filters in the dashboard query UI as needed:
  - **Project filter:** `project_id`
  - **Account/user filter:** `email`, `profile_id`, or `distinct_id`
  - **LLM flow filter:** `feature_path` (for LLM dashboard)

## Automation script (source of truth)

Script: `scripts/posthog_dashboard_bootstrap.py`

It is idempotent and safe to re-run:

```bash
POSTHOG_API_KEY=phx_... \
POSTHOG_HOST=https://us.posthog.com \
POSTHOG_PROJECT_ID=105300 \
python scripts/posthog_dashboard_bootstrap.py
```

What it does:
- upserts 4 dashboards by name
- upserts all insights by name under each dashboard
- prints resulting dashboard URLs/IDs for auditability

## Notes

- Cost trend tile uses `$ai_total_cost_usd`. If this property is not yet emitted in all flows, the chart may be sparse until instrumentation catches up.
- Logs are represented operationally through failure-event trends + ingestion heartbeat tiles so the team can detect reliability regressions quickly.
