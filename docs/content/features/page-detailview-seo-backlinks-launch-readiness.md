# Page DetailView SEO + Backlinks: Launch Readiness (v1)

## Scope
This note defines pre-launch safeguards for the paid Page DetailView feature set:

- Pro-gated Page Command Center (`project_page_detail`)
- SEO Analysis v1 (deterministic checks + JSON-LD guidance)
- Backlink discovery pipeline (Exa search + enrichment + cache)

It is the rollout/rollback reference for the first broad release.

---

## Internal architecture summary

### Request and gating path
1. User opens `project/<project_pk>/pages/<page_pk>/`.
2. `ProjectPageDetailView.get_queryset()` enforces project ownership.
3. `render_to_response()` returns **403** with upgrade CTA for non-Pro users.
4. Pro users get three sections: Overview, SEO Analysis, Backlink Opportunities.

### SEO analysis path
1. UI POST `action=run_seo_analysis`.
2. `start_or_reuse_run()` creates or reuses a run with:
   - active-run dedupe
   - rerun cooldown
3. Background task: `core.tasks.execute_project_page_analysis_run`.
4. Run execution:
   - fetch page content
   - analyze content
   - compute deterministic SEO payload (`analyze_project_page_seo`)
   - persist compact payload on `ProjectPageAnalysisRun`
5. DetailView renders latest successful payload + recent run history.

### JSON-LD path (within SEO analysis)
- `analyze_json_ld_schema()` extracts `application/ld+json` blocks.
- State model: `ok` / `issues` / `missing`.
- Handles malformed JSON safely (parse errors captured, no crash).
- Provides starter schema suggestion for missing/problem states.

### Backlink discovery path
1. DetailView reads cached candidates from Django cache.
2. Cache miss triggers async refresh enqueue (`refresh_backlink_prospects_cache`).
3. Worker runs `discover_backlink_prospects()`:
   - topic extraction from project/page metadata
   - Exa search per topic
   - score + filter + dedupe candidate pages
   - optional public-contact enrichment (contact page/email/social)
4. Results are cached with TTL and rendered in sortable/filterable table.

---

## Known limitations (v1)

1. **Heuristic scoring only**
   - SEO score is deterministic and explainable, but not a full technical SEO audit.
2. **JSON-LD validation is baseline**
   - Detects common structure issues, not full schema.org conformance across all types.
3. **Backlink source dependency**
   - Candidate quality/recency depends on Exa results and coverage.
4. **Contact enrichment confidence**
   - Public-signal extraction can return low-confidence matches or no contact routes.
5. **No hard guarantee of outreach success**
   - Candidates are suggestions; outreach viability remains editorial/manual.
6. **Event coverage is still maturing**
   - Operational logs and run records exist; product telemetry validation is part of post-launch checks.

---

## Troubleshooting notes

### SEO analysis run stays loading
- Check if an active run exists (`queued`/`running`).
- If stuck, inspect worker queue health and task execution logs.
- Retry after cooldown if prior run just finished.

### SEO analysis fails
- Review `ProjectPageAnalysisRun.failure_message` + `failure_details`.
- Common causes:
  - page fetch failed
  - parse/analyze exceptions in page processing
- Use “Retry analysis” after root-cause fix.

### JSON-LD shows issues unexpectedly
- Confirm page HTML snapshot includes `<script type="application/ld+json">` blocks.
- Validate JSON syntax and required keys (`@context`, `@type`, type-specific fields).
- Use starter suggestion as baseline, then customize canonical values.

### Backlink opportunities stay empty/loading
- Verify page has recent analyzed context (`date_analyzed` should be present).
- Check lock key is not stale (`backlink-prospects-refresh-lock-v1`).
- Confirm Exa API key is configured.
- Inspect worker logs for Exa request failures/timeouts.

### Backlink discovery error spikes
- Review outbound request success rate to Exa and enrichment targets.
- Reduce overcollection or enrichment budgets via `BACKLINK_PROSPECTS_CONFIG`.
- Temporarily disable refresh UI trigger if needed (rollback section).

---

## Staged rollout plan

### Stage 0 — Internal only
- Audience: internal/admin accounts
- Goals:
  - validate queue stability
  - validate run failure handling and UX messages
  - verify cache refresh lifecycle and lock cleanup

### Stage 1 — Beta (small Pro cohort)
- Audience: selected Pro users (low-volume)
- Goals:
  - confirm user-visible quality of recommendations
  - monitor provider cost and latency
  - gather support feedback on confusing outputs

### Stage 2 — Full Pro rollout
- Audience: all Pro users
- Goals:
  - maintain stable run success rates
  - keep provider costs within target envelope
  - ensure support load is manageable with documented limitations

---

## Success metrics and guardrails

### Success metrics
- SEO analysis run success rate (target: high 90s)
- Median time-to-first-result on DetailView
- Backlink candidate availability rate on analyzed pages
- User engagement:
  - refresh analysis clicks
  - refresh backlink discovery clicks
  - copy-contact action usage

### Guardrail metrics
- Exa request error rate and timeout rate
- Backlink enrichment timeout rate
- Queue depth / task lag for analysis + backlink jobs
- Cost per active Pro user (provider spend)
- Spike in support tickets tagged with Page DetailView/SEO/Backlinks

---

## Rollback plan

Trigger rollback if any of the following persist beyond alert window:
- sustained provider error spike
- unacceptable queue backlog or run failure rate
- provider cost spike beyond budget

### Rollback steps (ordered)
1. Disable user-triggered backlink refresh action (or short-circuit enqueue).
2. Stop automatic cache-miss enqueue for backlink discovery in DetailView.
3. Keep cached historical candidates visible if available.
4. If needed, disable SEO rerun action while preserving last successful payload display.
5. Announce degraded mode in release channel + support notes.
6. Ship follow-up patch before re-enabling in staged order.

---

## Post-launch validation checklist

### Telemetry and observability
- [ ] Confirm analysis/backlink task execution logs are populated.
- [ ] Confirm run history records are being created with expected statuses.
- [ ] Confirm dashboard/queries for success + guardrail metrics are populated.

### Production gating and access
- [ ] Verify free users receive 403 + upgrade CTA on DetailView.
- [ ] Verify Pro users can open DetailView and trigger refresh actions.

### Manual QA (real customer pages)
- [ ] Validate one healthy page: expected SEO score/check rendering.
- [ ] Validate missing JSON-LD page: missing state + starter suggestion.
- [ ] Validate malformed JSON-LD page: issues state + parse errors visible.
- [ ] Validate backlink table sorting/filtering and copy actions.
- [ ] Validate failed run UX with actionable retry messaging.

### Support readiness
- [ ] Share known limitations with support.
- [ ] Share troubleshooting paths and escalation points.
- [ ] Link this rollout note in the shipping PR.
