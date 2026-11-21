# API contracts

The API is a FastAPI service. Every REST path is versioned under `/api/v1`. The other mount
points are `/webhooks/*`, `/mcp`, `/healthz`, `/readyz` and `/dev/*`. The `/dev/*` routes exist
only when `APP_ENV=local` **and** `WHATSAPP_MODE=simulator`. The OpenAPI document is served at
`/api/v1/openapi.json`, with Swagger UI at `/api/v1/docs` when `APP_ENV != production`.

## Conventions

- Bodies are JSON, in camelCase on the wire and snake_case in Python.
- Errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details
  (`type`, `title`, `status`, `detail`, `code`), with stable `code` values.
- Money is `{"amountMinor": 7500, "currency": "USD"}`. Clients format it with the conversation
  locale. Rendered quote text from the server already carries a period label.
- Times are ISO 8601 UTC. Pagination uses cursors (`?limit=&cursor=` returning `nextCursor`), and
  message lists use `?after=<messageId>`.
- Rate limits are kept in memory per process in Phase 1. A limited request gets `429` with
  `Retry-After`.

| Limit | Value |
|---|---|
| Chat session creation | 10 per hour per IP address |
| Chat messages | 20 per minute per conversation and 60 per minute per IP address |
| Login | 5 per minute per IP address and per email |
| MCP calls | 120 per minute per token |
| Quote-producing MCP calls | 60 per hour per token |

## Routing matrix

| Path prefix | Compose (`web` nginx on :8080) | Vite dev (:5173) | AWS (CloudFront) |
|---|---|---|---|
| `/` and the SPA routes (`/simulator`, `/agent/*`, `/privacy`) | static build | Vite | S3 |
| `/api/*` | → api:8000 | → localhost:8000 | → ALB |
| `/webhooks/*` | → api:8000 | → localhost:8000 | → ALB |
| `/mcp` | → api:8000 | → localhost:8000 | → ALB |
| `/healthz`, `/readyz` | → api:8000 | → localhost:8000 | not routed (the ALB checks `/healthz` directly) |
| `/dev/*` | → api:8000, only while the API mounts it | → localhost:8000 | never routed |

Host ports in compose are bound to `127.0.0.1` only. Smoke tests use `http://localhost:8080` as
their single base URL.

## Auth (ADR-0014)

| Caller | Credential | Header |
|---|---|---|
| Customer (web) | Conversation JWT: `typ=customer`, `sub` = conversation ID, `ver` = `token_version`, 30-day **sliding** expiry | `Authorization: Bearer <jwt>` |
| Agent | Agent JWT: `typ=agent`, 60 minutes | `Authorization: Bearer <jwt>` |
| MCP client | API token with `mcp:read` / `mcp:write` | `Authorization: Bearer pp_…` |
| n8n | API token with `integrations` | `Authorization: Bearer pp_…` |
| Meta | HMAC signature | `X-Hub-Signature-256: sha256=<hex>` |

Every customer response carries a renewed token in `X-Conversation-Token`, and the SSE
`message.completed` event carries one in `token`. The browser keeps it in `localStorage`, so it
survives restarts. Erasure increments `token_version`, which revokes every token for that
conversation.

## Health

| Path | Behaviour |
|---|---|
| `GET /healthz` | `200 {"status":"ok"}`. Process only, no database, so a shared Aurora cluster can pause. |
| `GET /readyz` | `200` when the database is reachable, Alembic is at head and the embedder is loaded; `503` otherwise. |

## Customer chat

### `POST /api/v1/chat/sessions`

The optional body is `{"locale": "en"}`. The response is `201`:

```json
{
  "conversationId": "7d0c…",
  "token": "<jwt>",
  "locale": "en",
  "disclosure": "Hi! I'm PolicyPilot, an AI assistant for Harbor & Pine Insurance Agency (a fictional demo agency)…",
  "privacyUrl": "/privacy",
  "demoMode": true
}
```

`demoMode` is true when the scripted fake model is active (ADR-0017). The disclosure is also
stored as the first message.

### `GET /api/v1/chat/sessions/{id}/messages?after=<messageId>`

This returns `{status, messages: [...], nextAfter, hasMore}` for the conversation, in pages of
200 (`hasMore` is true for a full page; read on with `after=nextAfter`), where `status` is
`active`, `awaiting_review`, `human_takeover` or `closed`. Each message is
`{id, role, content, createdAt, citations[], artifacts[], actions[]}`, where `role` is `customer`,
`assistant`, `agent` or `system` and `actions` are quick replies (`{id, label}`, for example
"Submit" or "Change details"). Customer messages show the sanitised text.

The web client polls this every 3 seconds while the conversation is `awaiting_review` or
`human_takeover`, **only while the tab is visible**, and for no more than 15 minutes without user
activity. Through this endpoint, agent replies and review decisions reach web customers, including
after a reload.

### `POST /api/v1/chat/sessions/{id}/messages`: a turn

```http
Accept: text/event-stream
Content-Type: application/json

{"text": "I'm 34, married with two kids, budget around $80 a month for life cover", "clientMessageId": "c-01J…"}
```

- `clientMessageId` is required. It is a client-generated ULID or UUID and is unique per
  conversation. The optional `action` carries the `id` of a quick reply the customer clicked; the
  `text` is its label. A retry with the same ID returns the stored result as a short stream. If the turn
  is still running, it returns `409 turn_in_progress`.
- If another turn holds the conversation lock, the response is `409 turn_in_progress`.
- Message too long gives `413`. Too many concurrent turns on the process gives `503 overloaded`
  with `Retry-After: 5` (agent-design.md, concurrency).
- If the conversation is paused for review, the stream contains only the control-handling reply
  (agent-design.md).
- If the client disconnects, the turn **continues** to completion on the server. The client
  recovers the result with `GET …/messages`.

#### SSE events

Each event has an `event:` line and JSON `data:`. A `: ping` comment is sent every 15 seconds.
**No unvalidated model text is ever sent** (agent-design.md#delivering-the-reply-no-unvalidated-text).

| Event | Data | When |
|---|---|---|
| `turn.started` | `{turnId, runId}` | First |
| `status` | `{key, label}`, for example `{key:"quoting", label:"Calculating quotes for 3 products…"}` | Progress during the run |
| `artifact.quote` | `{quoteId, productId, productName, carrierName, premium: Money, billingPeriod, monthlyEquivalent: Money, variants[], breakdown[], disclaimer, expiresAt}` | Quotes built from stored rows |
| `artifact.comparison` | `{productIds[], columns[], rows[]}` | Comparison data from the database |
| `message.delta` | `{text}` | Chunks of the **validated** reply, after `guard_output` |
| `message.completed` | `{messageId, text, citations: [{chunkId, documentTitle, section, pages}], quoteIds[], actions[], conversationStatus, token}` | End of a successful turn |
| `error` | `{code, message, retryable}` | The turn failed. The customer message is kept and no assistant message is stored. Resend with the same `clientMessageId` to retry. |

### `GET /api/v1/catalog/products?line=life`

Public. It returns the active products with carrier, attributes and texts in the locale given by
`Accept-Language`, falling back to `en`.

## Agent dashboard

All of these need an agent JWT. Every write is audit-logged.

| Method and path | Purpose |
|---|---|
| `POST /api/v1/auth/login` `{email, password}` | Returns `{accessToken, expiresAt, agent}` or `401 invalid_credentials` |
| `GET /api/v1/auth/me` | The current agent |
| `GET /api/v1/agent/review-tasks?status=&kind=` | The review queue |
| `GET /api/v1/agent/review-tasks/{id}` | The task with its snapshot, quote and messages since the task was created, the decision and outcome, and `whatsappWindowOpen` (WhatsApp tasks only) |
| `POST /api/v1/agent/review-tasks/{id}/claim` | `open` → `claimed`. Returns `409` if someone else claimed it. |
| `POST /api/v1/agent/review-tasks/{id}/resolve` | Body `{action, notes?, clericalEdits?}`, where `action` is `approve`, `request_changes`, `reject`, `edit_and_approve`, `return_to_assistant` or `close`. The server attaches the intervening customer messages; the client never sends a transcript. Errors: `409 review_not_resolvable`, `409 conversation_busy` (a customer turn holds the lock; retry), `422 invalid_action`, `422 edits_need_edit_and_approve`, `422 requires_customer_confirmation` (edits beyond name, email and phone), `422 quote_expired`, and `502 resume_failed` (the decision is saved; use `retry-resume`). |
| `POST /api/v1/agent/review-tasks/{id}/retry-resume` | For tasks stuck in `resolving` (agent-design.md#resolving) |
| `POST /api/v1/agent/conversations/{id}/messages` `{text}` | Human reply during a `human_takeover`, under the conversation lock (`409 conversation_busy` while a customer turn runs; `404` for an erased conversation). The text is sanitised at ingress and delivered through the channel. On WhatsApp outside the 24-hour window it returns `409 whatsapp_window_closed`, with the `agent_follow_up` template offered instead. |
| `GET /api/v1/agent/conversations?status=&channel=` / `GET /api/v1/agent/conversations/{id}` | Conversations with messages, profile, quotes, applications and runs |
| `GET /api/v1/agent/runs/{runId}` | The trace |
| `GET /api/v1/agent/leads?status=&kind=` / `PATCH /api/v1/agent/leads/{id}` | Leads, with `contact` (email, phone) and `consent` (`at`, `source`, `textVersion`) so an agent can act on any lead, including one created through MCP |
| `GET /api/v1/agent/applications/{id}` | The application with all its snapshot versions |
| `GET /api/v1/agent/catalog/carriers`, `/products`, `/products/{id}/rate-tables` | Catalog and rate tables, read-only |
| `GET /api/v1/agent/outbox?status=dead` / `POST /api/v1/agent/outbox/{id}/retry` | n8n delivery problems |
| `GET /api/v1/agent/metrics/summary` | Dashboard counters |

The dashboard polls every 15 seconds, only while the tab is visible, and pauses after 15 minutes
of inactivity. It never polls in the background.

## Integrations (n8n)

These need an API token with the `integrations` scope.

| Method and path | Purpose |
|---|---|
| `GET /api/v1/integrations/leads/follow-ups-due?before=<iso>` | Lead IDs, the assigned agent's email and the line, for leads due a follow-up |
| `POST /api/v1/integrations/leads/{id}/follow-ups` `{occurrenceId, channel, note}` | **Idempotent** on `occurrenceId`, which is `<n8n execution id>:<lead id>`. A replay returns `200` with `{"duplicate": true}` and changes nothing. |
| `GET /api/v1/integrations/digest?date=YYYY-MM-DD` | Counts for the daily digest |

## Webhooks

| Method and path | Purpose |
|---|---|
| `GET /webhooks/whatsapp` | Verification handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`). Returns `200 text/plain` with the challenge, or `403`. |
| `POST /webhooks/whatsapp` | Verifies `X-Hub-Signature-256` over the raw body (max 1 MB), then **durably stores** the sanitised events before returning `200`. If storage fails, it returns `503`, so Meta retries (whatsapp.md). |

## Local-only (`APP_ENV=local` and `WHATSAPP_MODE=simulator`)

These routes are not mounted in any other configuration, and nginx forwards them only to an API
that mounts them.

| Method and path | Purpose |
|---|---|
| `POST /dev/whatsapp/simulate` `{userId, name, text}` or `{userId, buttonId}` | Builds a Meta-shaped payload, signs it with the local secret and posts it to `/webhooks/whatsapp` in-process. `userId` must start with `sim-`. |
| `GET /dev/whatsapp/outbox?userId=&after=` | Messages "sent" to the simulated user |
| `POST /dev/whatsapp/advance-clock` `{userId, hours}` | Moves `last_customer_message_at` back to demonstrate the 24-hour window |

## MCP

`/mcp` is the Streamable HTTP endpoint. See [mcp-server.md](mcp-server.md).
