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

#### `GET /public-api/projects` (List projects)

Returns projects owned by the API key account.

Query params:

- `page` (optional, default `1`, min `1`)
- `page_size` (optional, default `20`, min `1`, max `100`)

Success (`200`):

```json
{
  "status": "success",
  "projects": [
    {
      "project_id": 123,
      "name": "TuxSEO",
      "type": "SaaS",
      "url": "https://tuxseo.com",
      "summary": "AI-powered SEO content automation",
      "blog_theme": "",
      "founders": "",
      "key_features": "",
      "target_audience_summary": "",
      "pain_points": "",
      "product_usage": "",
      "links": "",
      "language": "english",
      "location": "Global"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1
  }
}
```

#### `POST /public-api/projects` (Create project)

Creates a project and triggers initial content analysis.

Request body:

```json
{
  "url": "https://example.com",
  "source": "public_api"
}
```

Success (`200`) response uses the same `project` object shape as above.

Common errors:

- `400` invalid input (`Project URL must start with http:// or https://`, duplicates, plan limits, unverified email gate)
- `500` unexpected creation/analysis failures

#### `GET /public-api/projects/{project_id}` (Get project)

Returns a single owned project.

Responses:

- `200` with project payload
- `404` if project is not found for this account

#### `PATCH /public-api/projects/{project_id}` (Update project)

Partially updates project fields.

Updatable fields:

- `name`, `summary`, `blog_theme`, `founders`, `key_features`
- `target_audience_summary`, `pain_points`, `product_usage`
- `links`, `language`, `location`

Responses:

- `200` updated project
- `400` no fields provided or invalid values (for example empty `name`)
- `404` project not found

### Title Suggestions

- `GET /public-api/projects/{project_id}/title-suggestions`
- `GET /public-api/projects/{project_id}/title-suggestions/{suggestion_id}`
- `POST /public-api/projects/{project_id}/title-suggestions`

### Keywords

- `GET /public-api/projects/{project_id}/keywords`
- `GET /public-api/projects/{project_id}/keywords/{keyword_id}`
- `POST /public-api/projects/{project_id}/keywords`

### Competitors

- `GET /public-api/projects/{project_id}/competitors`
- `GET /public-api/projects/{project_id}/competitors/{competitor_id}`
- `POST /public-api/projects/{project_id}/competitors`

### Project Pages

- `GET /public-api/projects/{project_id}/pages`
- `GET /public-api/projects/{project_id}/pages/{page_id}`
- `POST /public-api/projects/{project_id}/pages`

### Blog Posts

- `POST /public-api/projects/{project_id}/blog-posts/generate`
- `GET /public-api/projects/{project_id}/blog-posts`
- `GET /public-api/projects/{project_id}/blog-posts/{blog_post_id}`
- `POST /public-api/projects/{project_id}/blog-posts/{blog_post_id}/publish`

## Plan-Based Access Rules

### Free-allowed endpoints/actions

- Read account/projects/resources (`GET` endpoints)
- Create first project (`POST /public-api/projects`) within free project limit
- Create title suggestions (`POST /title-suggestions`) within free monthly limit
- Generate blog posts (`POST /blog-posts/generate`) within free monthly limit
- Add competitors/pages within free limits

### Pro-gated or upgrade-required actions

- Creating projects beyond free limit (`FREE_PLAN_PROJECT_LIMIT_REACHED`)
- Keyword additions (`PRO_PLAN_REQUIRED_KEYWORD_ADDITION`)
- Content automation configuration (`PRO_PLAN_REQUIRED_CONTENT_AUTOMATION`)
- Hitting free usage caps for title/blog generation and competitors (`*_LIMIT_REACHED`)

When a plan gate blocks a request, API returns deterministic `403` with machine-readable error code:

```json
{
  "status": "error",
  "code": "PRO_PLAN_REQUIRED_KEYWORD_ADDITION",
  "message": "Keyword additions are not available on the Free plan. Upgrade to Pro to add custom keywords.",
  "upgrade_url": "https://tuxseo.com/pricing"
}
```

## Request Examples

### Get account

```bash
curl -X GET "https://tuxseo.com/public-api/account" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

### List projects

```bash
curl -X GET "https://tuxseo.com/public-api/projects?page=1&page_size=20" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

### Get project details

```bash
curl -X GET "https://tuxseo.com/public-api/projects/123" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

### Create project

```bash
curl -X POST "https://tuxseo.com/public-api/projects" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "url": "https://example.com",
    "source": "public_api"
  }'
```

### Update project metadata

```bash
curl -X PATCH "https://tuxseo.com/public-api/projects/123" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "name": "TuxSEO",
    "summary": "SEO automation platform for founders",
    "language": "english",
    "location": "Global"
  }'
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

### Add a competitor

```bash
curl -X POST "https://tuxseo.com/public-api/projects/123/competitors" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $TUXSEO_API_KEY" \
  -d '{
    "url": "https://competitor.com",
    "analyze_now": true
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
