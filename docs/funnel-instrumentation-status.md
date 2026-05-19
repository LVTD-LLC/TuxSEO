# Funnel Instrumentation Status (Todoist 6g5R6WPr433mFqm7)

## Canonical funnel events

- `signup_started`
- `signup_completed`
- `email_verified`
- `project_create_succeeded`
- `first_blog_generated`
- `checkout_started`
- `checkout_succeeded`

## Trigger locations and key properties

- `signup_started`
  - Trigger: `frontend/templates/account/signup.html` on first form focus or submit.
  - Properties: `trigger`, `path`.
- `signup_completed`
  - Trigger: `core/views.py` in `AccountSignupView.form_valid`.
  - Properties: `$set.email`, `$set.username` (+ standard backend event properties).
- `email_verified`
  - Trigger: `core/signals.py` on `allauth.account.signals.email_confirmed`.
  - Properties: `email_domain` (+ standard backend event properties).
- `project_create_succeeded`
  - Trigger: `core/models.py` in `Profile.get_or_create_project` when `created=True`.
  - Properties: `source`, `profile_id`, `profile_email`, `project_id`, `project_name`, `project_url`.
- `first_blog_generated`
  - Trigger: `core/models.py` in `BlogPostTitleSuggestion.generate_content` when first generated blog post for the profile is created.
  - Properties: `project_id`, `blog_post_id`, `title_suggestion_id`, `content_type`.
- `checkout_started`
  - Trigger: `core/views.py` in `create_checkout_session` before Stripe checkout session creation.
  - Properties: `product_name`, `price_id`, `source`.
- `checkout_succeeded`
  - Trigger: `core/webhooks.py` in `handle_checkout_completed`.
  - Properties: `checkout_id`, `mode`, `payment_status`, `subscription_id`.

## Data quality checks

- Taxonomy loader now validates:
  - missing `events` object
  - malformed event/alias names
  - missing `stage`/`description`
  - duplicate names
  - alias targets that do not exist
- `track_event` now rejects unknown events and requires a valid taxonomy definition.
- Backend-captured events include `event_schema_version` and `event_stage` for stable funnel queries.

## Funnel query examples (HogQL)

```sql
SELECT
  event,
  toDate(timestamp) AS day,
  count() AS events
FROM events
WHERE event IN (
  'signup_started',
  'signup_completed',
  'email_verified',
  'project_create_succeeded',
  'first_blog_generated',
  'checkout_started',
  'checkout_succeeded'
)
GROUP BY event, day
ORDER BY day DESC, event ASC;
```

```sql
SELECT
  person.properties.email AS email,
  minIf(timestamp, event = 'signup_started') AS signup_started_at,
  minIf(timestamp, event = 'signup_completed') AS signup_completed_at,
  minIf(timestamp, event = 'email_verified') AS email_verified_at,
  minIf(timestamp, event = 'project_create_succeeded') AS project_create_succeeded_at,
  minIf(timestamp, event = 'first_blog_generated') AS first_blog_generated_at,
  minIf(timestamp, event = 'checkout_started') AS checkout_started_at,
  minIf(timestamp, event = 'checkout_succeeded') AS checkout_succeeded_at
FROM events
WHERE event_schema_version = 1
GROUP BY person.properties.email
ORDER BY signup_started_at DESC
LIMIT 1000;
```
