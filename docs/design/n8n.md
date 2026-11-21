# n8n workflows and events

The decision is ADR-0012. n8n runs as `n8nio/n8n:2.48.6`. The workflows live as exported
JSON in `workflows/n8n/` and are imported and published automatically by the
`n8n-import` compose service.

## Events the API emits

The API writes each event to `app.outbox_events` in the same transaction as the domain change.
The dispatcher then POSTs it to `N8N_EVENTS_URL` (locally `http://n8n:5678/webhook/policypilot-events`).

Headers:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-PolicyPilot-Event-Id` | The outbox UUID, which is also the idempotency key |
| `X-PolicyPilot-Event-Type` | For example `lead.created` |
| `X-PolicyPilot-Token` | `N8N_WEBHOOK_TOKEN`, checked by the Webhook node's header auth |

Every body uses the same envelope:

```json
{
  "id": "3f1e…",
  "type": "application.submitted",
  "occurredAt": "2026-11-20T10:15:00Z",
  "data": { "...": "event-specific, see below" },
  "links": { "dashboard": "/agent/reviews/9a2b…" }
}
```

| Event | When | `data` (no sensitive PII) |
|---|---|---|
| `lead.created` | A lead is created by the assistant (application start or explicit contact request) or by MCP `create_lead` | `leadId`, `source`, `kind`, `lineInterest[]`, `customerDisplayName` (first name only), `customerId` |
| `application.submitted` | The customer confirms submission and a review task opens | `reviewTaskId`, `applicationId`, `productName`, `conversationId`, `channel`, `customerId` |
| `application.reviewed` | An agent approves, rejects or requests changes | `applicationId`, `decision`, `reviewedBy` (agent email), `customerId` |
| `handoff.requested` | The customer asks for a human | `reviewTaskId`, `conversationId`, `channel`, `reason`, `priority`, `customerId` |
| `handoff.resolved` | An agent returns the conversation to the assistant or closes it | `reviewTaskId`, `resolution`, `customerId` |

`links.dashboard` is a path in the web app; the workflows prefix it with `APP_BASE_URL` (rendered
into the workflow at import). Premiums are deliberately left out of events: the agent sees them in
the dashboard.

Delivery behaviour:

- Delivery is at-least-once, with backoff (2 s × 2^attempt, capped at 10 minutes, 10 attempts).
  After that the event is dead-lettered and shown in the dashboard.
- Dispatching is **event-driven**. After commit, the request handler nudges the in-process
  dispatcher. A periodic sweep runs only while undelivered events exist, and once at start-up. An
  idle system never polls the database, which keeps a shared Aurora cluster able to pause
  (`docs/deployment/aws.md`).
- `N8N_EVENTS_URL` empty disables dispatch. Events stay pending, which tests rely on.

## Workflows

### `events-router.json`

- **Trigger:** Webhook node `POST /webhook/policypilot-events`, with header-auth credential
  `X-PolicyPilot-Token`.
- **Steps:**
  1. Best-effort deduplication. The Code node "Drop repeats" marks an event as a duplicate when its
     ID is among the last 500 handled IDs in workflow static data. The node "Remember event" adds
     the ID only **after** the email was sent, so a failed attempt is not mistaken for a handled
     one. Delivery is **at-least-once**, so a staff email can occasionally be sent twice (after a
     crash between sending and acknowledging). That is acceptable for staff notifications. Anything
     that changes data in the API is idempotent on the API side instead.
  2. Switch on `duplicate`, then `type`:
     - a duplicate → stop (the webhook still answers `200`);
     - `lead.created` → email to the agency inbox: "New lead from {source}";
     - `application.submitted` → email: "Review needed: {productName}", with the dashboard link;
     - `handoff.requested` → email with a `[High priority]` subject prefix, followed by a
       **disabled** Slack placeholder (a disabled node passes items through) and a sticky note
       explaining how to connect Slack;
     - other events (`application.reviewed`, `handoff.resolved`) → no email. This is where a CRM
       update would go in Phase 2.
  3. The webhook answers with the last node's output (`responseMode: lastNode`). If an email fails,
     the execution fails and the webhook answers `500`, so the API's dispatcher retries with
     backoff instead of marking the event delivered.
- **Email:** an SMTP credential that points to Mailpit locally (`mailpit:1025`, no auth). The
  agency inbox address is `agents@policypilot.test`, a reserved TLD that cannot leak mail.

### `lead-follow-up.json`

- **Trigger:** Schedule, at 08:00 and 14:00 UTC (cron `0 8,14 * * *`). Kept infrequent for the
  shared-database plan.
- **Steps:**
  1. `GET /api/v1/integrations/leads/follow-ups-due?before={{now}}` with an HTTP Header Auth
     credential (`Authorization: Bearer <integrations token>`).
  2. For each lead, email the assigned agent (or the agency inbox): "Follow up with lead {id}
     (created {age} ago)".
  3. `POST /api/v1/integrations/leads/{id}/follow-ups` with
     `occurrenceId = "{{$execution.id}}:{{leadId}}"`. The API records the follow-up and moves
     `nextFollowUpAt` forward 48 hours **once per occurrence ID** (table `integration_follow_ups`).
     A retried or replayed execution therefore cannot advance the date twice.

### `daily-digest.json`

- **Trigger:** Schedule, daily at 08:00 UTC.
- **Steps:** `GET /api/v1/integrations/digest?date={{yesterday}}`, then email a summary to the
  agency inbox: conversations, leads, applications by status, average review time, fallback rate
  and dead-lettered events.

## Unattended bootstrap

Workflows and credentials must be in place, and published, before the n8n server starts. The
running server registers production webhooks only for workflows that are published when it
loads them. The sequence in compose is:

1. `db` is healthy. The `n8n` database and role come from `docker/postgres/init.sh`.
2. **`n8n-import`** is a one-shot container from the same image, with the same `DB_*` variables and
   the **same `N8N_ENCRYPTION_KEY`**, which the init script generates. It runs
   `docker/n8n/import.sh`:
   1. it renders `credentials.json` into a temporary directory from environment variables, with
      **fixed credential IDs**:
      - `pp-smtp-mailpit` (SMTP, Mailpit);
      - `pp-webhook-auth` (Header Auth, `X-PolicyPilot-Token = N8N_WEBHOOK_TOKEN`);
      - `pp-api-token` (HTTP Header Auth, `Authorization: Bearer N8N_INTEGRATION_TOKEN`);
   2. `n8n import:credentials --input=<tmp>/credentials.json`;
   3. it renders the workflows into the same temporary directory, replacing three placeholders:
      `__POLICYPILOT_API_URL__` (`http://api:8000`), `__APP_BASE_URL__` (the web app, for links in
      emails) and `__AGENCY_INBOX__` (`agents@policypilot.test`);
   4. `n8n import:workflow --separate --input=<tmp>/workflows`. The workflows have fixed IDs
      (`pp-events-router`, `pp-lead-follow-up`, `pp-daily-digest`) and refer to the credentials by
      those fixed IDs;
   5. `n8n publish:workflow --id=<id>` for each workflow;
   6. it deletes the temporary directory.
   Running it again is safe: imports upsert by ID. Re-running it while n8n is up also works; the
   running server picks up the newly published versions within seconds.
3. **`n8n`** starts only after `n8n-import` has exited successfully (`depends_on: condition:
   service_completed_successfully`). It loads the published workflows and registers their webhooks.
   Its healthcheck is `GET /healthz/readiness`.
4. The API's outbox dispatcher does not depend on n8n being up. Events stay pending and are retried
   with backoff until the webhook answers. Nothing is lost if n8n starts later.

Ownership: imported workflows and credentials belong to n8n's initial owner project. When n8n
first runs with no owner, it holds them for the instance until someone completes the owner setup
in the editor (the first visit to `http://localhost:5678`). The published webhooks work before that
setup is done. This was verified with n8n 2.48.6 (an event posted to the webhook before any owner
setup produced an email in Mailpit), is covered by the compose smoke test (`make smoke` posts an
event and checks Mailpit), and must be re-checked when the n8n image is upgraded.

n8n 2.48.6 logs that PostgreSQL 16 "receives compatibility support only" and recommends 17 or
newer. The local stack keeps PostgreSQL 16 to match the Aurora target, and n8n on AWS uses SQLite,
so this only affects the local n8n database. Revisit it when the Aurora target moves to 17.

**AWS (`docs/deployment/aws.md`):** n8n runs with SQLite on ephemeral task storage, so the same
import runs at **every** task start (an entrypoint wrapper). After a task is replaced, the workflows,
credentials and published state are recreated. Execution history, the deduplication cache and
editor-only changes are lost, which is acceptable for a demo; workflow changes are made in Git, not
in the AWS editor.

## Local URLs

| What | URL |
|---|---|
| n8n editor | `http://localhost:5678`. Complete the owner setup on first visit. |
| Mailpit (caught emails) | `http://localhost:8025` |

## Editing workflows

Edit in the n8n UI, then export with `make n8n-export`, which runs
`n8n export:workflow --backup --output=/workflows`. Before you commit, strip the instance-specific
fields; the `make` target does this with `jq`. Review the diff. Exported JSON must never contain
credentials, so the target fails if it finds `"data":` under `credentials`.

## MCP from n8n (Phase 2)

n8n's MCP Client Tool node can connect to `http://api:8000/mcp` with an `mcp:read` token. A demo
workflow that asks an n8n AI Agent node to compare products through PolicyPilot's MCP tools is
deferred to Phase 2. In Phase 1, MCP is demonstrated with Claude Code and the SDK smoke client.
