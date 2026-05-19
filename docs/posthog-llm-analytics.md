# PostHog LLM analytics (PydanticAI generation flows)

TuxSEO now emits PostHog **LLM analytics** events (`$ai_generation`) for every `run_agent_synchronously(...)` execution used by our PydanticAI generation flows.

## What is captured

For each run we send:

- `$ai_model` (resolved from the active PydanticAI model)
- `$ai_latency` (seconds)
- `$ai_input` (sanitized/truncated prompt preview)
- `$ai_output_choices` (sanitized/truncated output preview on success)
- token metrics when available from PydanticAI usage:
  - `$ai_input_tokens`
  - `$ai_output_tokens`
  - `$ai_total_tokens`
- `feature_path` (`<model_name>.<function_name>`) for flow-level grouping
- `result_status` (`succeeded` or `failed`)
- failure metadata (`error_type`, `error_message`) on errors

This enables:

- comparing model performance/cost by feature path
- diagnosing failed/slow runs with contextual metadata
- using PostHog LLM Analytics views (Generations/Traces) alongside product events and logs

## Configuration

LLM analytics piggybacks on existing PostHog backend config:

- `POSTHOG_API_KEY` (required)
- `POSTHOG_INGEST_HOST` (defaults to `https://us.i.posthog.com`)

If `POSTHOG_API_KEY` is unset, the LLM analytics emission is skipped.

## Verification checklist

1. Trigger generation flows (e.g. title generation, content generation, project analysis).
2. Open PostHog → **LLM Analytics**.
3. Confirm rows appear with `feature_path` and `result_status`.
4. Filter by `feature_path` to compare latency/tokens across flows.
