# PostHog Logs integration

TuxSEO ships structured backend logs to PostHog Logs using OTLP over HTTP.

## What is logged

All structlog events can be forwarded with consistent searchable fields:

- `level`
- `event` (message)
- `timestamp`
- `environment`
- `service`
- `module`
- `request_id`
- `trace_id`
- `user_id` (when available)
- `project_id` (when available)
- `task_id` / `job_id` (background jobs)
- `exception_type`
- `stack_trace`

## Correlation coverage

- **Web requests**: `RequestLogContextMiddleware` binds request and trace IDs, then logs request completion/failure.
- **Background jobs**: task helpers bind `task_id` and domain IDs (`project_id`, `job_id`).
- **AI generation flows**: generation tasks bind task context before model/content generation calls.

## Redaction and safety

Before export, logs are scrubbed to prevent accidental secret/PII leakage:

- Sensitive keys (token/password/secret/cookie/etc.) are replaced with `[REDACTED]`.
- Email-like strings are replaced with `[REDACTED_EMAIL]`.
- Bearer token payloads and PostHog keys (`phc_...`) are removed.

## Runtime behavior

### Local/dev

Recommended defaults:

- `POSTHOG_LOGS_ENABLED=false`
- keep console logs for debugging
- no remote OTLP shipping

### Production

Recommended defaults:

- `POSTHOG_LOGS_ENABLED=true`
- `POSTHOG_API_KEY` set
- `POSTHOG_LOGS_ENDPOINT` points to ingest host `/v1/logs`

Batch exporter settings are tunable:

- `POSTHOG_LOGS_BATCH_MAX_QUEUE_SIZE`
- `POSTHOG_LOGS_BATCH_MAX_EXPORT_BATCH_SIZE`
- `POSTHOG_LOGS_BATCH_SCHEDULE_DELAY_MILLIS`
- `POSTHOG_LOGS_BATCH_EXPORT_TIMEOUT_MILLIS`

These settings keep ingestion async/batched so request latency is not blocked by log shipping.
