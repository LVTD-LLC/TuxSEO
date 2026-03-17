---
title: API · Project Pages
description: Project page endpoints for the TuxSEO Public API.
---

## Endpoints

- `GET /public-api/projects/{project_id}/pages`
- `GET /public-api/projects/{project_id}/pages/{page_id}`
- `POST /public-api/projects/{project_id}/pages`

Use these endpoints to manage a project's "Your Pages" URLs.

## Sitemap auto-refresh

Projects with a valid `sitemap_url` are now refreshed periodically by a background scheduler.
Removed URLs are marked stale (not hard-deleted) to keep history while preventing duplicate inserts.

### Session-auth manual trigger

- `POST /api/project/{project_id}/sitemap/sync-now/`

This is an authenticated app endpoint (not Public API) and queues an immediate sync for that project.

## Canonical API Reference

- `/api/docs` (tag: **Project Pages**)
- `/api/openapi.json`
