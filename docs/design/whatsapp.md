# WhatsApp channel

The decision is ADR-0009. The platform facts used here were verified on 2026-11-20 and are listed in
[verified-facts.md](../reference/verified-facts.md#whatsapp-business-platform-cloud-api).

## Configuration

| Variable | Meaning |
|---|---|
| `WHATSAPP_MODE` | `simulator` (default) or `cloud` |
| `WHATSAPP_GRAPH_VERSION` | Default `v27.0`. Configurable because Meta's docs show several versions. |
| `WHATSAPP_PHONE_NUMBER_ID` | From the App Dashboard (cloud mode) |
| `WHATSAPP_ACCESS_TOKEN` | A system-user token with `whatsapp_business_messaging` (cloud mode) |
| `WHATSAPP_APP_SECRET` | Verifies `X-Hub-Signature-256`. In simulator mode, a random local value. |
| `WHATSAPP_VERIFY_TOKEN` | The string entered in the App Dashboard when registering the webhook |

None of these appear in the repository. The init script generates the local simulator values.

## Inbound flow (durable acceptance)

1. **Verification (GET):** if `hub.mode == "subscribe"` and `hub.verify_token` matches (constant-time),
   return `200 text/plain` with `hub.challenge`. Otherwise return `403`.
2. **Signature (POST):** read the raw body (at most 1 MB). Compute `HMAC_SHA256(app_secret, raw_body)`
   and compare it in constant time with the hex after `sha256=`. If they differ, return `401`; the
   body is neither parsed nor logged.
3. **Parse and sanitise:** Pydantic models (`channels/whatsapp/payloads.py`), with unknown fields
   ignored. Each message is reduced to the fields needed:
   - provider message ID;
   - user ID (`user_id`, `from_user_id` or `from`);
   - profile name;
   - timestamp;
   - type;
   - text or button ID.
   The text goes through **ingress sanitisation** (security-and-privacy.md). Status updates keep
   only the ID, status, timestamp and error code.
4. **Durable acceptance:** in one short transaction, `INSERT INTO inbound_events … ON CONFLICT
   (channel, provider_message_id) DO NOTHING` for every event in the batch.
   - **Only after commit** does the handler return `200`.
   - If the insert fails (database down or still resuming), it returns `503`, and Meta retries for
     up to 7 days.
   - A duplicate delivery hits the conflict and is acknowledged without creating a second event.
5. **Process:** after commit, the handler nudges the in-process drainer for each affected
   conversation.
   - The drainer takes the conversation lock (non-blocking).
   - It processes that conversation's `pending` events in `(received_at, id)` order. For each event
     it sets `processing`, runs the turn (agent-design.md), then sets `done` or `failed` with
     `attempts` and `last_error`.
   - Failed events are retried up to 3 times with backoff, then left `failed` for the dashboard.
6. **Recovery:** on start-up, and every 60 seconds **only while** pending or processing events
   older than 2 minutes exist, the drainer re-queues stuck events. An idle system issues no polling
   queries.

Processing details:

| Item | Behaviour |
|---|---|
| Identity | `(whatsapp, user_id or wa_id)` gives the customer. The profile name is stored as the display name. |
| Conversation | The latest non-closed WhatsApp conversation for that identity, or a new one. A new conversation sends the `ai_disclosure` first. |
| 24-hour window | `last_customer_message_at` is set from the message timestamp, which opens the window. |
| `text` | Starts a turn |
| `interactive.button_reply` | Maps to its button ID, for example `confirm_submit` |
| Images, audio, location and other types | `unsupported_content` template |
| `statuses[]` | Update `outbound_messages` by `provider_message_id`. A failure records the error code. |

## Outbound delivery

- Every reply first becomes an `outbound_messages` row (`pending`), in the same transaction as the
  stored assistant message.
- A sender delivers pending rows, in order per conversation, through the adapter:
  - `cloud` mode: `POST https://graph.facebook.com/{version}/{phone_number_id}/messages`;
  - `simulator` mode: insert into `simulator_outbox`.
- Delivery retries on 429 and 5xx with backoff, up to 5 attempts. A 4xx is a permanent failure,
  recorded and shown in the dashboard.
- The returned `wamid` is stored. Status webhooks move rows to `delivered`, `read` or `failed`.

## Outbound rendering

| Artifact | WhatsApp rendering |
|---|---|
| Reply text | WhatsApp formatting (`*bold*`), split at 4,000 characters on paragraph boundaries, at most 3 messages |
| Quote card | `*{product}* by {carrier}`, then the **server-rendered price with its period**: "$75.00 per month (estimate)", or for auto "$1,518.00 per year (about $126.50 per month, estimate)". Then the top 3 breakdown lines and the disclaimer. |
| Comparison | A numbered list per product with 4 key attributes (no tables) |
| Confirmation step | Reply buttons, at most 3 (titles up to 20 characters): `Submit` / `Change details` / `Talk to agent` |
| Citations | `Source: {document}, §{section}, p.{page}` |

## 24-hour customer service window

`WindowPolicy.can_send_freeform(conversation, now)` is
`now - last_customer_message_at < 24h - 5min`.

| Situation | Behaviour |
|---|---|
| Reply to a customer message | Free-form |
| Review decision, window open | Free-form decision message |
| Review decision, window closed | Template `application_update` (utility, factual status only) |
| Human agent reply, window closed | The API refuses the free-form send (`409 whatsapp_window_closed`); the dashboard offers the `agent_follow_up` template |
| Marketing or renewal messages | Not sent in Phase 1 |

The template definitions live in `data/seed/message_templates.yaml`. That file also documents the
exact text to submit for approval in WhatsApp Manager.

## Policy guardrails specific to WhatsApp

- **Scope:** only agency topics; out-of-scope requests get the refusal template.
- **Disclosure:** the AI disclosure is the first message of every conversation.
- **Sensitive data:** the assistant never requests card numbers, bank account numbers, ID numbers or
  health information. If a customer volunteers them, **ingress sanitisation removes** them (including
  health details) before storage or any model call. The customer then gets `sensitive_data_warning`
  or `health_data_notice`, which offers a private call with an agent.
- **Escalation:** the keyword "agent" or "human", or the `Talk to agent` button, works at any time.
- **Opt-out:** "STOP" sets `customers.whatsapp_opted_out_at` and sends `opt_out_confirmed`. While
  it is set, no delivery intent is recorded (agent replies and review decisions included), and
  intents queued before STOP are marked `failed` (`opted_out`) instead of being sent. Any later
  message from the customer lifts the opt-out, as the confirmation says.
- **Recovery:** a pending inbound event whose turn already completed (a crash before the event was
  marked done) is replayed, not run again. Stored redaction counts are reused, so the redaction
  notices and sensitive-only routing work as on the web. A failed send is retried with backoff,
  and the sender schedules itself for the earliest retry.

## Running locally without Meta

1. `python scripts/init_env.py`, then `docker compose up --build`.
2. Open `http://localhost:8080/simulator`. It shows a phone with a fake user ID, `sim-…`.
3. Typing sends `POST /dev/whatsapp/simulate`. The API builds a Meta-shaped payload, **signs it with
   the local app secret** and posts it to `/webhooks/whatsapp`. Every step above runs.
4. Outbound messages appear from `simulator_outbox`. The page polls once a second while visible,
   and stops after 15 minutes idle. Buttons are clickable.
5. "Advance clock 25h" closes the window, so the template fallback can be seen.

## Connecting a real Meta test number (optional)

1. Create a Meta app with the WhatsApp product, which provides a test business number, and add
   tester phone numbers. The App Dashboard shows how many are allowed.
2. Set `WHATSAPP_MODE=cloud` and the four secrets in `.env`, then restart the API. **In cloud mode,
   the `/dev` routes are not mounted**, so the signing bypass the simulator uses does not exist.
3. Expose **only the API's webhook path** over public HTTPS. The `docker-compose.tunnel.yml`
   override adds a `webhook-gateway` (nginx, `docker/nginx/tunnel.conf`) on `127.0.0.1:8081` that
   forwards `GET`/`POST /webhooks/whatsapp` and answers `404` for every other path, so the web app,
   the agent API, MCP and `/dev` are unreachable through the tunnel. Point the tunnel at
   `http://127.0.0.1:8081`, never at the web app on 8080. Meta rejects self-signed certificates.

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d
   ```
4. Register `https://<tunnel>/webhooks/whatsapp` and the verify token in the App Dashboard, and
   subscribe to `messages`.

## Cost note

Since 2026-10-01, Meta charges per delivered message. Each business number gets 1,000 free
service messages a month, and utility templates are charged even inside the window. The dashboard
shows outbound messages this month.
