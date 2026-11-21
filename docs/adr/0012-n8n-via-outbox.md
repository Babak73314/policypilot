# ADR-0012: n8n automations fed by a transactional outbox

- Status: Accepted
- Date: 2026-11-20

## Context

Agency staff want notifications and follow-ups (email, Slack, CRM, reminders) that they
can change without a code release. n8n is a fixed choice. The API must not lose events,
for example when n8n is down, and must not send an event for a change that later rolled back.

## Decision

- Domain changes that matter to automations write an event to `app.outbox_events` **in the same
  transaction** as the change. The event types are:
  - `lead.created`
  - `application.submitted`, when a review is requested
  - `application.reviewed`, when a review is approved, rejected or returned for changes
  - `handoff.requested`
  - `handoff.resolved`
- A dispatcher runs as an asyncio background task in the API process. It reads the outbox with
  `FOR UPDATE SKIP LOCKED` and POSTs each event to `N8N_EVENTS_URL` with these headers:
  - `X-PolicyPilot-Event-Id`, for idempotency;
  - `X-PolicyPilot-Event-Type`;
  - `X-PolicyPilot-Token`, a shared secret checked by the n8n Webhook node's header auth.
  Failed deliveries retry with exponential backoff up to 10 attempts and then stay in a dead-letter
  state that the dashboard shows. Delivery is at-least-once.
- Event payloads carry **IDs and non-sensitive fields only**: product, status and a dashboard link.
  n8n calls back to the API's integration endpoints (`/api/v1/integrations/*`, API token with scope
  `integrations`) when it needs more.
- n8n workflows are kept as exported JSON in `workflows/n8n/`:
  - `events-router`: one webhook that switches on the event type and sends staff emails.
  - `lead-follow-up`: a schedule that asks the API for leads due a 48-hour follow-up and sends
    reminders.
  - `daily-digest`: a schedule that sends a morning summary through the integration API.
- Locally, n8n sends email to **Mailpit**, a fake SMTP server with a web UI, so nothing leaves the machine.
- A one-shot compose service imports the workflows and publishes them with the n8n CLI
  (`import:workflow`, then `publish:workflow`). n8n 2.x replaced activation with publish/unpublish.
- n8n runs from `n8nio/n8n:2.48.6` under its Sustainable Use License. Internal agency use is fine.
  The project does not offer n8n as a hosted service to others.

## Consequences

- Automations change without API deploys, and the API stays correct even when n8n is down.
- Delivery is at-least-once. Anything that **changes data** is idempotent on the API side: follow-up
  callbacks carry an `occurrenceId` with a unique constraint. Staff notification emails deduplicate
  on a best-effort basis (a static-data cache in n8n), and a rare duplicate email is accepted
  (review 01, finding 23).
- Workflows and credentials are imported and published **before** the n8n server starts, with fixed
  IDs and a shared encryption key, so the bootstrap is unattended (n8n.md).
- n8n gets its own Postgres database locally. On AWS it may use SQLite to avoid holding connections
  to a shared Aurora cluster (`docs/deployment/aws.md`).

## Alternatives considered

- **Calling n8n directly from request handlers:** events are lost when n8n is down, and events are
  sent for changes that rolled back.
- **SQS between the API and n8n:** the right step at scale (Phase 2), but more infrastructure now.
- **Make or Zapier:** hosted, paid, and not self-hostable.
