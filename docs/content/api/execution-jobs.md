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

## Status lifecycle

Execution jobs use this canonical status model:

- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

## Idempotency requirements

For create and retry endpoints, pass `Idempotency-Key` header. Reusing the same key for the same operation returns the same job instead of creating duplicates.

## Structured error envelope

Execution endpoints return machine-readable errors:

```json
{
  "status": "error",
  "code": "MISSING_IDEMPOTENCY_KEY",
  "message": "Provide an Idempotency-Key header for job creation."
}
```

## Canonical API Reference

- `/api/docs` (tag: **Execution Jobs**)
- `/api/openapi.json`
