# Detail View Paid Modules — Ops Notes

## What shipped

- Feature flags
  - `DETAIL_VIEW_SEO_ANALYSIS_ENABLED`
  - `DETAIL_VIEW_BACKLINK_DISCOVERY_ENABLED`
  - `DETAIL_VIEW_CONTACT_ENRICHMENT_ENABLED`
- API-layer guardrails
  - Daily quotas for manual module runs
  - Backlink discovery cooldown
  - Per-profile action rate limit window
- Runtime safeguards
  - Exa provider retry + exponential backoff
  - Configurable provider request timeout
  - Structured backlink failure debug payload in cache
- Admin debug visibility
  - Staff-only section on page detail with failed SEO run metadata and latest backlink debug state

## Monitoring checklist

1. **Telemetry health**
   - Verify `detail_view_opened`, `seo_analysis_run_*`, `backlink_discovery_*`, `opportunities_viewed`, `contact_method_copied` are visible in PostHog.
   - Confirm property schema consistency (`project_id`, `project_page_id`, `result_status`).

2. **Failure rate**
   - Track spikes in `seo_analysis_run_failed` and `backlink_discovery_failed`.
   - Inspect staff debug panel for `failure_reason`, `error_type`, and provider context.

3. **Quota/rate behavior**
   - Check support tickets for unexpected "daily limit" or "too many requests" messages.
   - Tune:
     - `DETAIL_VIEW_SEO_ANALYSIS_DAILY_LIMIT`
     - `DETAIL_VIEW_BACKLINK_DISCOVERY_DAILY_LIMIT`
     - `DETAIL_VIEW_BACKLINK_DISCOVERY_COOLDOWN_SECONDS`
     - `DETAIL_VIEW_ACTION_RATE_LIMIT_*`

4. **Provider resilience**
   - Tune:
     - `BACKLINK_PROSPECTS_EXA_REQUEST_TIMEOUT_SECONDS`
     - `BACKLINK_PROSPECTS_PROVIDER_MAX_RETRIES`
     - `BACKLINK_PROSPECTS_PROVIDER_RETRY_BACKOFF_SECONDS`
     - `BACKLINK_PROSPECTS_PROVIDER_RETRY_BACKOFF_MAX_SECONDS`

## Rollback plan (safe order)

1. Immediately disable expensive modules if needed:
   - `DETAIL_VIEW_BACKLINK_DISCOVERY_ENABLED=false`
   - optionally `DETAIL_VIEW_CONTACT_ENRICHMENT_ENABLED=false`
2. Keep SEO module on unless incident also affects SEO analysis:
   - `DETAIL_VIEW_SEO_ANALYSIS_ENABLED=true`
3. If UX friction is too high, temporarily relax quotas/cooldowns.
4. If telemetry queueing causes concerns, no hard rollback needed (tracking is best-effort and non-blocking).
5. Re-enable progressively:
   - SEO → backlink discovery (without enrichment) → contact enrichment.
