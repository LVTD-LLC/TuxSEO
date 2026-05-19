# SEO Analysis Run Retention Strategy

## What we store

For each page-level SEO analysis run (`ProjectPageAnalysisRun`), we persist:

- **Lifecycle metadata:** trigger, status, queued/started/finished timestamps.
- **Ownership context:** project, project page, requesting profile.
- **Compact SEO payload snapshot:**
  - score + pass/warn/fail counters
  - check list fields needed by the UI (`label`, `status`, `value`, `why_it_matters`, `how_to_fix`)
  - JSON-LD summary and starter suggestion when available
- **Failure debugging fields:** human-readable `failure_message` + structured `failure_details`.
- **Payload integrity metadata:** `payload_checksum` (SHA-256) + `payload_bytes`.

We intentionally do **not** copy raw markdown/page blobs into the run record. Source content remains on `ProjectPage` and run payloads stay compact.

## Retention policy

Current behavior keeps run records indefinitely for now to support audit/debugging and recent-history UI.

Recommended follow-up once volume grows:

1. Keep full payloads for the latest **30 days**.
2. For older runs, keep metadata + checksum/failure fields and clear heavy payload sections.
3. Add periodic cleanup command (daily/weekly) with idempotent batches.

This staged approach preserves debugging value while keeping storage growth predictable.
