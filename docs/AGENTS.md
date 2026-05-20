# Documentation Guidelines For AI Agents

These instructions apply to files under `docs/`. Root-level engineering guidance still comes from
`../AGENTS.md`.

## Audience

- Write for TuxSEO users first. Explain what they can accomplish before describing how the system is
  implemented.
- Use plain language and short paragraphs. Avoid framework jargon unless the document is explicitly
  for contributors.
- Prefer concrete examples, expected outcomes, and screenshots or references when they help a user
  confirm success.

## Structure

- Put the most useful answer near the top.
- Use descriptive headings that match the user's goal.
- Use numbered steps for procedures and bullets for options, requirements, or caveats.
- Call out prerequisites before the steps that depend on them.
- Keep troubleshooting sections symptom-driven: describe what the user sees, the likely cause, and
  the action to take.

## Style

- Use active voice and direct verbs such as "Create", "Configure", "Review", and "Publish".
- Avoid saying "simply", "obviously", or "just" for work that may be unfamiliar to users.
- Keep product names, command names, environment variables, and file paths exact.
- Do not expose secrets or private operational details in user-facing docs.

## Maintenance

- Update docs in the same change as user-facing behavior changes.
- Remove stale instructions instead of appending corrections below them.
- If a document duplicates README or in-app copy, keep the most durable version here and link to it
  from other places when practical.
