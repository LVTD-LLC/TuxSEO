# PostHog Event Coverage Matrix (TuxSEO)

Last updated: 2026-03-19

## Scope

Critical P1 funnel and product actions requested for reliable conversion/product analysis.

## Coverage matrix

| Flow | Canonical event(s) | Source of truth | Required properties |
|---|---|---|---|
| Signup | `signup_completed` | Server (`AccountSignupView.form_valid`) | (taxonomy optional) |
| Login | `login_succeeded` | Server (`signals.capture_login_succeeded`) | `auth_provider`, `result_status` |
| Project create | `project_create_succeeded` | Server (`Profile.get_or_create_project`) | `project_id`, `project_url`, `result_status` |
| Integration connect | `integration_connected` | Server (`ProjectIntegrationsGoogleCallbackView._save_google_integration`, `ProjectIntegrationsView._connect_plausible`) | `project_id`, `provider`, `result_status` |
| Integration disconnect | `integration_disconnected` | Server (`ProjectIntegrationsView._disconnect_google_integration`, `_disconnect_plausible`) | `project_id`, `provider`, `result_status` |
| Keyword updates | `keyword_updated` | Server (`add_keyword_to_project`, `toggle_project_keyword_use`, `delete_project_keyword`) | `project_id`, `keyword_id`, `update_action`, `result_status` |
| Page analysis | `page_analysis_completed` | Server (`ProjectPage.analyze_content`) | `project_id`, `project_page_id`, `result_status` |
| Title generation | `title_generation_completed` | Server (`generate_title_suggestions`, `generate_title_from_idea`) | `project_id`, `content_type`, `title_count`, `result_status` |
| Content generation | `content_generation_succeeded`, `content_generation_failed` | Server (`tasks.generate_blog_post_content`) | success: `project_id`, `title_suggestion_id`, `blog_post_id`, `result_status`; failure: `project_id`, `title_suggestion_id`, `result_status` |
| Publish | `publish_attempted`, `publish_succeeded`, `publish_failed` | Server (`post_generated_blog_post`) | `project_id`, `blog_post_id`, `result_status` |
| Link exchange toggle | `link_exchange_toggled` | Server (`toggle_link_exchange`) | `project_id`, `enabled`, `result_status` |
| Plan upgrade | `plan_upgraded` | Server webhook (`handle_updated_subscription`) | `plan`, `result_status` |
| Plan cancel | `plan_cancelled` | Server webhook (`handle_updated_subscription`) | `plan`, `result_status` |
| Onboarding complete | `onboarding_completed` | Server (`Profile.get_or_create_project`, first project only) | (taxonomy optional) |
| First content generated | `first_content_generated` | Server (`BlogPostTitleSuggestion.generate_content`, first generated post) | (taxonomy optional) |
| Subscription start | `subscription_created`, `subscription_started`, `paid_conversion` | Server webhook (`handle_created_subscription`) | (taxonomy optional) |
| Analytics page usage telemetry | `analytics_page_viewed`, `analytics_date_range_changed`, `analytics_refresh_clicked`, `analytics_source_error_shown` | Frontend (`frontend/src/controllers/analytics_dashboard_controller.js`) | page viewed: `project_id`, `date_range_start`, `date_range_end`, `range_days`; date range changed: + `change_source`; refresh clicked: same as page viewed; source error shown: `project_id`, `source`, `error_message`, `result_status` |

## Audit summary (before this change)

Previously tracked reliably: signup, project create, checkout started/completed, some first-time content events.

Gaps that were closed:

- login success event missing
- integration connect/disconnect events missing
- keyword mutation events missing
- page-analysis completion events missing
- title-generation completion events missing
- content generation success/failure events missing
- publish attempt/success/failure events missing
- link-exchange toggle event missing
- explicit plan upgrade/cancel lifecycle events missing

## Regression guardrails

- Taxonomy-based required-property validation in `track_event`
- Attribution schema enrichment + malformed-payload guardrails in `track_event`
- Test coverage in `core/tests/test_posthog_event_coverage.py` and `core/tests/test_paid_acquisition_attribution.py`
