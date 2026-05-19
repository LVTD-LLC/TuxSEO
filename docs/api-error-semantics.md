# API Error Semantics (Entitlements + Ownership)

This document defines deterministic error codes/messages for plan gating and ownership checks.

## Shared Error Shape

Error payloads use:

- `status`: always `"error"`
- `code`: stable machine-readable code
- `message`: human-readable message
- `upgrade_url` (optional): when upgrading plan can unblock the request

## Ownership/Scope Errors

- `RESOURCE_NOT_FOUND`
  - Returned when a requested resource does not exist **or is outside the caller's ownership scope**.
  - This avoids cross-tenant resource enumeration.

## Plan Entitlement Errors

- `FREE_PLAN_PROJECT_LIMIT_REACHED`
- `FREE_PLAN_TITLE_SUGGESTION_LIMIT_REACHED`
- `FREE_PLAN_BLOG_POST_LIMIT_REACHED`
- `PRO_PLAN_REQUIRED_CONTENT_AUTOMATION`
- `PRO_PLAN_REQUIRED_OG_IMAGE_GENERATION`
- `PRO_PLAN_REQUIRED_LINK_EXCHANGE`
- `PRO_PLAN_REQUIRED_KEYWORD_ADDITION`
- `PLAN_COMPETITOR_LIMIT_REACHED`
- `PLAN_COMPETITOR_POST_LIMIT_REACHED`

Fallback/support codes:

- `PROJECT_LIMIT_REACHED`
- `KEYWORD_LIMIT_REACHED`

## Implementation Notes

- Entitlement evaluation is centralized in `core/api_error_semantics.py`.
- Public API and authenticated internal API both call the same entitlement evaluator.
- Mutating/read-sensitive endpoints use ownership-scoped lookups and return deterministic ownership errors.
