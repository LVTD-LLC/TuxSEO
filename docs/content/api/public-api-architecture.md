---
title: Public API Architecture
description: How TuxSEO separates external public automation APIs from internal app APIs.
---

## Overview

TuxSEO has two API surfaces:

- Internal API: `/api/*` for first-party web app behavior.
- Public API: `/public-api/*` for external automation clients.

This keeps internal endpoints stable for product development while giving external users a focused, documented contract.

## Authentication

Public API requests use an API key header:

- Header: `X-API-Key: <your_api_key>`

You can find your API key in **Settings → API Access**.

## Documentation Exposure

Public docs are available at:

- `GET /api/docs`
- `GET /api/openapi.json`

Internal OpenAPI docs are intentionally disabled.
Legacy paths (`/public-api/docs`, `/public-api/openapi.json`) redirect to these canonical URLs.

## Public Endpoints

### Account

- `GET /public-api/account`

### Projects

- `POST /public-api/projects`
- `GET /public-api/projects/{project_id}`
- `PATCH /public-api/projects/{project_id}`

### Content Automation

- `POST /public-api/projects/{project_id}/content-automation`

### Title Suggestions

- `GET /public-api/projects/{project_id}/title-suggestions`
- `GET /public-api/projects/{project_id}/title-suggestions/{suggestion_id}`
- `POST /public-api/projects/{project_id}/title-suggestions`

### Keywords

- `GET /public-api/projects/{project_id}/keywords`
- `GET /public-api/projects/{project_id}/keywords/{keyword_id}`
- `POST /public-api/projects/{project_id}/keywords`

### Project Pages

- `GET /public-api/projects/{project_id}/pages`
- `GET /public-api/projects/{project_id}/pages/{page_id}`
- `POST /public-api/projects/{project_id}/pages`

### Blog Posts

- `POST /public-api/projects/{project_id}/blog-posts/generate`
- `GET /public-api/projects/{project_id}/blog-posts`
- `GET /public-api/projects/{project_id}/blog-posts/{blog_post_id}`
- `POST /public-api/projects/{project_id}/blog-posts/{blog_post_id}/publish`

## Request Examples

### Get account

```bash
curl -X GET "https://tuxseo.com/public-api/account" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

### Create title suggestions

```bash
curl -X POST "https://tuxseo.com/public-api/projects/123/title-suggestions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "count": 5,
    "content_type": "SHARING",
    "seed_guidance": "focus on founder-led growth"
  }'
```

### Add a page URL to "Your Pages"

```bash
curl -X POST "https://tuxseo.com/public-api/projects/123/pages" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "url": "https://example.com/pricing",
    "analyze_now": true
  }'
```

### List project pages

```bash
curl -X GET "https://tuxseo.com/public-api/projects/123/pages?page=1&page_size=20" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

Page responses include core metadata for integrations: URL, source, inferred page type, title, description, summary, always-use flag, and scrape/analyze timestamps.

### Generate a blog post from a title suggestion

```bash
curl -X POST "https://tuxseo.com/public-api/projects/123/blog-posts/generate" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "title_suggestion_id": 456
  }'
```

### List blog posts without content payload

```bash
curl -X GET "https://tuxseo.com/public-api/projects/123/blog-posts?include_content=false&page=1&page_size=20" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

### Publish a generated blog post

```bash
curl -X POST "https://tuxseo.com/public-api/projects/123/blog-posts/789/publish" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

## Design Notes

- Public handlers reuse existing models and business rules where possible.
- Validation happens at schema and endpoint layers with explicit error responses.
- Ownership checks are enforced at the project/resource level for all endpoints.
- Internal/private API details are intentionally excluded from public docs.
