<p align="center"><img src="https://minio-api.cr.lvtd.dev/tuxseo-prod/logo512.png" width="230" alt="TuxSEO Logo"></p>

<div align="center">

<img src="https://minio-api.cr.lvtd.dev/tuxseo-prod/logo-large.png" width="230" alt="TuxSEO Name">

<b>Automated Blog Content Creation for Founders Who Hate Writing</b>
</div>

***

## Overview

- TuxSEO learns about your business, analyzes the market which lets you...
- Generate content ideas for you business blog to drive traffic from searches.
- Stop wasting time and money on research and writing, let TuxSEO do it for you.
- TuxSEO is open-source, self-hostable. Always.
- Run it privately on [your computer](#deployment) or try it on our [cloud app](https://tuxseo.com).

***

## TOC

- [Overview](#overview)
- [TOC](#toc)
- [Deployment](#deployment)
  - [Render](#render)
  - [Docker Compose](#docker-compose)
  - [Pure Python / Django deployment](#pure-python--django-deployment)
  - [Custom Deployment on Caprover](#custom-deployment-on-caprover)
- [Local Development](#local-development)
- [Star History](#star-history)


## Deployment

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/rasulkireev/tuxseo)

The only required env vars are:
- OPENAI_API_KEY
- TAVILY_API_KEY
- GEMINI_API_KEY
- PERPLEXITY_API_KEY
- JINA_READER_API_KEY
- KEYWORDS_EVERYWHERE_API_KEY

The rest are optional.

**Note:** This should work out of the box with Render's free tier if you provide the AI API keys. Here's what you need to know about the limitations:

- **Worker Service Limitation**: The worker service is not a dedicated worker type (those are only available on paid plans). For the free tier, I had to use a web service through a small hack, but it works fine for most use cases.

- **Memory Constraints**: The free web service has a 512 MB RAM limit, which can cause issues with **automated background tasks only**. When you add a project, it runs a suite of background tasks to analyze your website, generate articles, keywords, and other content. These automated processes can hit memory limits and potentially cause failures.

- **Manual Tasks Work Fine**: However, if you perform tasks manually (like generating a single article), these typically use the web service instead of the worker and should work reliably since it's one request at a time.

- **Upgrade Recommendation**: If you do upgrade to a paid plan, use the actual worker service instead of the web service workaround for better automated task reliability.

**Reality Check**: The website functionality should be usable on the free tier - you'll only pay for API costs. Manual operations work fine, but automated background tasks (especially when adding multiple projects) may occasionally fail due to memory constraints. It's not super comfortable for heavy automated use, but perfectly functional for manual content generation.

If you know of any other services like Render that allow deployment via a button and provide free Redis, Postgres, and web services, please let me know in the [Issues](https://github.com/rasulkireev/tuxseo/issues) section. I can try to create deployments for those. Bear in mind that free services are usually not large enough to run this application reliably.


### Docker Compose

This should also be pretty streamlined. On your server you can create a folder in which you will have 2 files:

1. `.env`

Copy the contents of `.env.example` into `.env` and update all the necessary values.

2. `docker-compose.yml`

Copy the contents of `docker-compose-prod.yml` into `docker-compose.yml` and run the suggested command from the top of the `docker-compose-prod.yml` file.

How you are going to expose the backend container is up to you. I usually do it via Nginx Reverse Proxy with `http://tuxseo-backend-1:80` UPSTREAM_HTTP_ADDRESS.


### Pure Python / Django deployment

Not recommended due to not being too safe for production and not being tested by me.

If you are not into Docker or Render and just wanto to run this via regular commands you will need to have 5 processes running:
- `python manage.py collectstatic --noinput && python manage.py migrate && gunicorn ${PROJECT_NAME}.wsgi:application --bind 0.0.0.0:80 --workers 3 --threads 2`
- `python manage.py qcluster`
- `npm install && npm run start`
- `postgres`
- `redis`

You'd still need to make sure .env has correct values.

### Custom Deployment on Caprover

1. Create 4 apps on CapRover.
  - `tuxseo`
  - `tuxseo-workers`
  - `tuxseo-postgres`
  - `tuxseo-redis`

2. Create a new CapRover app token for:
   - `tuxseo`
   - `tuxseo-workers`

3. Add Environment Variables to those same apps from `.env`.

4. Create a new GitHub Actions secret with the following:
   - `CAPROVER_SERVER`
   - `CAPROVER_APP_TOKEN`
   - `WORKERS_APP_TOKEN`
   - `REGISTRY_TOKEN`

5. Then just push main branch.

6. Github Workflow in this repo should take care of the rest.

## Local Development

All the information on how to run, develop and update your new application can be found in the documentation.

1. Update the name of the `.env.example` to `.env` and update relevant variables.
2. Run `make serve`
3. Run `make restart-worker` just in case, it sometimes has troubles connecting to REDIS on first deployment.

### CI pre-PR runbook (required before opening a PR)

Run the same pytest command as CI locally:

```bash
make test-ci
```

What this does:
- Boots local Postgres/Redis services used by tests.
- Runs `pytest` with the same strict flags as CI (`--strict-config --strict-markers`).
- Pins `PYTHONHASHSEED=0` for deterministic hash ordering.

If this fails, fix locally before pushing.

### Analytics ingestion jobs (GA4, GSC, Plausible)

TuxSEO now ships background ingestion for connected analytics providers:
- Google Analytics 4 (GA4)
- Google Search Console (GSC)
- Plausible

Implementation highlights:
- Incremental sync cursor per `(project, provider, source_account_ref)` with a rolling lookback window.
- Raw source snapshots (`AnalyticsSourceSnapshot`) + normalized daily facts (`AnalyticsFactDaily`).
- Idempotent upsert semantics for safe retries.
- Provider-aware retry/backoff behavior with observable cursor status and sanitized errors.
- Missing/disconnected integrations are skipped without failing the whole scheduling run.
- Project Home includes an **Analytics (GA4/GSC/Plausible)** section that surfaces:
  - connected-source status badges
  - 30-day KPI rollups (clicks, impressions, sessions, users, conversions)
  - derived rates (CTR, engagement, conversion) and avg GSC position
  - trend deltas (recent 7d vs prior 7d)
  - top low-CTR/high-impression opportunities with actionable on-page SEO suggestions

Scheduled entrypoint:
- `core.scheduled_tasks.schedule_project_analytics_syncs`

Worker task entrypoint:
- `core.tasks.sync_project_integration_analytics(project_id, provider)`

### Custom post types for blog idea generation

Projects can now define reusable **custom post types** under the Posts sidebar:
- open **Posts → Manage Types** (or click the `+` action next to Posts)
- create a type with:
  - a unique, clean name (validated per project)
  - prompt guidance (required, max length enforced)
- each custom type appears directly in Posts navigation
- selecting a custom type opens a dedicated idea-generation page that applies its guidance automatically when generating suggestions

### Deterministic content quality evaluation

Run the deterministic rubric evaluation locally:

```bash
make test-content-quality
```

This executes `core/tests/test_content_quality_evaluation.py`, scores `good`/`medium`/`bad` fixtures with fixed rubric weights, and writes `artifacts/content-quality-report.json`.

Safe baseline update procedure:

1. Run `make test-content-quality` and confirm the change is expected.
2. Review the generated `artifacts/content-quality-report.json` score deltas.
3. Re-run with explicit baseline update mode:
```bash
UPDATE_CONTENT_QUALITY_BASELINE=1 make test-content-quality
```
4. Commit `core/tests/fixtures/content_quality_baseline.json` together with the scoring logic change.

## Internal API (BlogPost CRUD)

Internal blog post management endpoints are available under `/api/internal/blog-posts` and are protected by the **superuser API key** query param (`?api_key=...`).

- `POST /api/blog-posts/submit` — create (existing endpoint, kept for compatibility)
- `GET /api/internal/blog-posts` — list
- `GET /api/internal/blog-posts/{id}` — retrieve
- `PUT /api/internal/blog-posts/{id}` — full update
- `PATCH /api/internal/blog-posts/{id}` — partial update
- `DELETE /api/internal/blog-posts/{id}` — delete

Non-superuser or missing API keys are rejected with unauthorized responses.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=rasulkireev/tuxseo&type=Date)](https://www.star-history.com/#rasulkireev/tuxseo&Date)
