# AGENTS.md

This is the repository-level guide for AI coding agents. Keep it tool-agnostic, concise, and focused
on facts an agent needs to work safely in this codebase.

## How To Use This File

- Treat this file as the shared source of truth for agent behavior in this repository.
- Keep instructions in standard Markdown. Do not rely on agent-specific syntax, imports, rule
  metadata, slash commands, or hidden configuration.
- Prefer updating this file over adding tool-specific files such as IDE rules, product-specific memory
  files, or duplicated prompt fragments.
- Add nested `AGENTS.md` files only when a subtree needs materially different instructions. The
  closest `AGENTS.md` to the edited file should take precedence.
- User instructions in chat override this file. If instructions conflict, follow the most specific
  applicable instruction and call out the conflict.
- Remove stale instructions instead of appending corrections below them.

## Project Snapshot

- TuxSEO is a Django 5 application running on Python 3.11.
- The backend is intentionally a single Django app: `core`.
- Runtime services include PostgreSQL, Redis, `django-q2`, `django-ninja`, `structlog`, `logfire`,
  `dj-stripe`, and `pydantic-ai`.
- The frontend uses Django templates, Stimulus 3, Tailwind CSS 3, Bootstrap 5, and Webpack.
- Python dependencies use Poetry. Frontend dependencies use npm with `package-lock.json`.
- Deployment is Docker-based, with separate CapRover/GitHub Actions flows for web and worker images.

## Working Rules

- Inspect relevant files before editing. Prefer `rg` or `rg --files` for repository search.
- Keep changes small, local, and consistent with existing Django, template, Stimulus, and CSS patterns.
- Preserve user work. Do not discard, reset, or overwrite unrelated changes.
- Avoid destructive git commands unless the user explicitly asks for them.
- Do not edit `.env` or commit secrets. Update `.env.example` when configuration requirements change.
- When adding dependencies, explain why the existing stack is insufficient.
- In final responses, summarize changed files, verification performed, and any remaining risk.

## Common Commands

Use Docker-backed `make` commands when verifying runtime behavior:

```bash
make serve
make shell
make bash
make manage "migrate"
make makemigrations
make test
make restart-worker
make stripe-sync
```

Use Poetry for Python checks:

```bash
poetry run pytest
poetry run ruff check .
poetry run ruff format .
poetry run djlint --check .
```

Use npm for frontend assets:

```bash
npm run start
npm run watch
npm run build
```

Do not use `pnpm` unless the repository intentionally switches package managers and updates the
lockfile.

## Verification

- Run the smallest relevant check that can catch the risk introduced by a change.
- For Django behavior changes, prefer focused pytest targets before broader suites.
- For template or style changes, run `djlint` when formatting risk is meaningful.
- For frontend asset changes, run the relevant npm build or dev check when practical.
- For docs and agent-instruction changes, a read-through plus `git diff --check` is usually enough.
- If verification cannot run because services, secrets, or dependencies are missing, state the exact
  blocker.
- Add or update tests when behavior changes, a bug is fixed, or manual verification would be weak.

## Architecture

- Keep the single-app architecture. Add backend code under `core/` unless a new Django app has a clear
  product boundary.
- Keep views focused on request handling, permissions, form orchestration, and response selection.
- Put domain rules in models, forms, focused helpers, or service modules based on the owner of the
  behavior.
- Prefer server-rendered Django templates. Add Stimulus only for behavior that needs JavaScript.
- Use `django-ninja` and Pydantic schemas for API boundaries.
- Use `django-q2` for background work. Tasks should be idempotent where practical.
- Use Django ORM features before raw SQL. Watch for N+1 queries and use `select_related`,
  `prefetch_related`, annotations, or transactions when they make behavior safer.

## Python And Django

- Use descriptive, full-word names. Boolean names should read naturally with `is_`, `has_`, or
  `can_`.
- Keep functions at one level of abstraction. If a helper is hard to name, split the responsibilities.
- Use guard clauses for invalid or exceptional cases, while keeping control flow readable.
- Prefer empty collections over `None` for collection results. Do not use mutable default arguments.
- Keep constants near the domain that owns them.
- Use timezone-aware datetimes with `django.utils.timezone`.
- Catch specific exceptions close to the boundary that can recover from them.
- Log unexpected failures with `structlog` and include actionable context such as model ids, task ids,
  provider names, project ids, and user ids.

## AI Features

- AI workflows live in `core/agents/`.
- Prefer structured Pydantic outputs over parsing free-form model text.
- Treat AI responses as untrusted until validated and normalized.
- Keep prompts discoverable in prompt modules. Avoid hiding behavior in long string concatenation.
- Keep provider-specific details behind small helpers so call sites stay model-agnostic.
- Store durable AI state in explicit model fields, not only in prompts or transient task state.
- Handle provider failures, timeouts, empty responses, and invalid structured output with clear
  fallbacks and logs.

## Frontend

- Prefer semantic HTML and Django template components over large JavaScript surfaces.
- Use Stimulus values, targets, classes, and actions before manual DOM queries.
- New controllers belong in `frontend/src/controllers/` and should follow existing controller style.
- Clean up timers, observers, subscriptions, and global event listeners in `disconnect()`.
- Use custom DOM events for decoupled sibling controllers. Use outlets for intentional parent-child
  relationships.
- Favor Tailwind utilities and existing Bootstrap patterns. Add custom CSS only when it is clearer
  than repeated utilities or required for complex states.
- Preserve accessibility with labels, real buttons, keyboard behavior, focus states, and useful alt
  text.
- Sanitize user-generated or AI-generated HTML before rendering it.

## Migrations And Data

- Create migrations for model changes.
- Only edit an existing migration when it is your own uncommitted migration and doing so is safer than
  adding a follow-up migration.
- Keep migrations deterministic. Do not call external services from migrations.
- Make data migrations restartable when possible and document assumptions in the migration code.

## Documentation

- User-facing docs should explain workflows and outcomes before implementation details.
- Internal docs should record decisions future maintainers need, not restate obvious code.
- Keep command names, environment variables, file paths, and product names exact.
