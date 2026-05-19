# Signup abuse gating + verified-email controls

## Goal
Reduce low-quality/bot signups and prevent unverified users from consuming expensive resources (LLM, crawling, enrichment, image generation).

## Implemented controls

### 1) Signup anti-abuse controls

Implemented in `core/forms.py` via `CustomSignUpForm` + `core/abuse_prevention.py`:

- **Turnstile challenge** (already present, retained)
  - Uses `CLOUDFLARE_TURNSTILE_SITEKEY` + `CLOUDFLARE_TURNSTILE_SECRET_KEY`.
- **IP-based signup rate limit**
  - Cache-backed sliding window counter per source IP.
  - Defaults:
    - `SIGNUP_RATE_LIMIT_ATTEMPTS_PER_IP=8`
    - `SIGNUP_RATE_LIMIT_WINDOW_SECONDS=3600`
- **Disposable-domain filtering**
  - Blocks common disposable domains plus configured blocklist.
  - Configurable via:
    - `SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST`

### 2) Verified-email gate for expensive actions

Implemented in `core/abuse_prevention.py` and enforced in `core/api/views.py`.

When `REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS=true`, unverified users are blocked from:

- project creation / analysis
- title generation
- blog content generation
- sitemap processing
- pricing page analysis
- competitor analysis and competitor comparison generation
- keyword enrichment endpoints
- OG image generation

Users receive a clear error message instructing them to verify email from Settings.

### 3) Observability

- Structured warning logs on every guardrail trigger with action + profile metadata.
- `abuse_guardrail_triggered` analytics event emitted when verified-email gate blocks an expensive action.
  - Properties:
    - `guardrail_reason` (currently: `unverified_email`)
    - `guardrail_action`

## Configuration

Added env configuration:

- `REQUIRE_VERIFIED_EMAIL_FOR_EXPENSIVE_ACTIONS` (default: `true`)
- `SIGNUP_RATE_LIMIT_ATTEMPTS_PER_IP` (default: `8`)
- `SIGNUP_RATE_LIMIT_WINDOW_SECONDS` (default: `3600`)
- `SIGNUP_DISPOSABLE_EMAIL_DOMAIN_BLOCKLIST` (comma-separated)

See `.env.example` for defaults.

### Turnstile troubleshooting checklist (production)

If signup shows a successful Turnstile challenge client-side but server-side verification fails:

1. Verify environment variables are present in runtime (CapRover app env):
   - preferred: `CLOUDFLARE_TURNSTILE_SITEKEY`
   - preferred: `CLOUDFLARE_TURNSTILE_SECRET_KEY`
2. Legacy aliases are also supported for backwards compatibility:
   - `CLOUDFLARE_TURNSTILE_SITE_KEY`
   - `TURNSTILE_SITE_KEY`
   - `CLOUDFLARE_TURNSTILE_SECRET`
   - `TURNSTILE_SECRET_KEY`
3. Ensure site key and secret belong to the same Turnstile widget.
4. Ensure widget hostname list includes `tuxseo.com`.
5. In logs, inspect Turnstile `reason_code` and `error_codes` to distinguish:
   - `token_missing`
   - `token_invalid`
   - `token_expired`
   - `provider_error`
   - `validation_mismatch`

## Validation notes

### Tests added

- `core/tests/test_abuse_prevention.py`
  - request IP extraction
  - signup rate limiting
  - disposable domain detection
  - verified-email gate behavior (verified vs unverified)
- `core/tests/test_api_verified_email_gate.py`
  - unverified users blocked from expensive API endpoints
- `core/tests/test_forms.py`
  - signup rate-limit blocking
  - disposable-domain blocking

### Suggested production checks

Track after deploy (24h/7d):

1. **Abuse reduction**
   - count of `abuse_guardrail_triggered` grouped by `guardrail_reason` and action.
   - ratio of blocked expensive-action requests to total expensive-action requests.
2. **False-positive/conversion impact**
   - `signup_completed` count/day vs previous baseline.
   - verification completion rate (verified users / signups).
   - support tickets related to signup verification failures.

If false positives are high, tune rate-limit thresholds upward and trim disposable-domain blocklist exceptions.
