---
title: API · Execution Jobs
description: Asynchronous execution lifecycle endpoints for agent-triggered jobs in the TuxSEO Public API.
---

## Endpoints

- `POST /public-api/projects/{project_id}/executions`
- `GET /public-api/executions`
- `GET /public-api/executions/{job_id}`
- `POST /public-api/executions/{job_id}/cancel`
- `POST /public-api/executions/{job_id}/retry`
- `POST /public-api/executions/{job_id}/rollback`

## Status lifecycle

Execution jobs use this canonical status model:

- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

## Idempotency requirements

For create and retry endpoints, pass `Idempotency-Key` header. Reusing the same key for the same operation returns the same job instead of creating duplicates.

## Structured failure envelope (Reliability UX v1)

Execution and publish flows return a normalized failure payload that maps to one taxonomy category:

- `validation`
- `policy`
- `dependency`
- `timeout`
- `quota`
- `unknown`

```json
{
  "status": "error",
  "code": "MISSING_IDEMPOTENCY_KEY",
  "message": "Provide an Idempotency-Key header for job creation.",
  "failure": {
    "taxonomy_version": "v1",
    "category": "validation",
    "code": "MISSING_IDEMPOTENCY_KEY",
    "message": "Provide an Idempotency-Key header for job creation.",
    "retryable": false,
    "fix_required": true,
    "remediation_hints": [
      "Send an Idempotency-Key request header when creating or retrying execution jobs."
    ],
    "next_actions": ["provide_idempotency_key", "retry_request"]
  }
}
```

Execution job resources now include:

- `failure` (latest normalized failure, if any)
- `history` (status transition timeline with details)
- `rollback` (operation-specific rollback hook state)

## Rollback hook

`POST /public-api/executions/{job_id}/rollback` currently supports `GENERATE_BLOG_POST` jobs that have succeeded.

Behavior:

- Deletes generated **draft** post created by that execution.
- Returns idempotent success when rollback already completed.
- Blocks auto-rollback for externally published posts and returns a policy failure with manual remediation.

## Canonical API Reference

- `/api/docs` (tag: **Execution Jobs**)
- `/api/openapi.json`
