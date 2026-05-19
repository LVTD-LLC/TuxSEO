---
title: API Authorization
description: How to authenticate requests to the TuxSEO Public API.
---

Use your TuxSEO API key in the `X-API-Key` header for every request.

- Header: `X-API-Key: <your_api_key>`
- API key location: **Settings → API Access**

```bash
curl -X GET "https://tuxseo.com/public-api/account" \
  -H "X-API-Key: $TUXSEO_API_KEY"
```

## Canonical API Reference

- Interactive docs: `GET /api/docs`
- OpenAPI schema: `GET /api/openapi.json`

Legacy endpoints (`/public-api/docs`, `/public-api/openapi.json`) redirect to canonical `/api/*` URLs.
