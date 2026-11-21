# ADR-0009: WhatsApp Cloud API behind a channel adapter, with a local simulator

- Status: Accepted
- Date: 2026-11-20

## Context

WhatsApp is a fixed channel. Meta's Cloud API is free to call (messages are billed),
has no intermediary and gives a test number. Developers and reviewers who clone the
repository will not have Meta credentials, a public HTTPS URL or a verified business.
The channel also has rules the web chat does not:

- free-form replies only within 24 hours of the customer's last message, templates otherwise;
- 4,096 characters per text message and no Markdown tables;
- no requests for card numbers, ID numbers or similar.

## Decision

- Define a **`Channel` port** with these operations: `send_text`, `send_template`, `send_interactive`
  (reply buttons) and `capabilities` (maximum length, Markdown support, button limits). The graph
  produces channel-neutral output, a reply text plus structured artifacts such as quote cards and
  comparison tables, and each adapter renders it for its channel.
- There are three adapters:
  - `web`: replies stream over SSE, and artifacts are sent as JSON events.
  - `whatsapp_cloud`: `httpx` calls to `POST https://graph.facebook.com/{version}/{phone_number_id}/messages`.
    The Graph API version is a setting, defaulting to `v27.0`.
  - `whatsapp_simulator`: outbound messages go to a `simulator_outbox` table that the web app's
    "WhatsApp simulator" page shows as a phone screen.
- **Inbound messages always go through the real webhook code.** The simulator builds a Meta-shaped
  payload, signs it with the local app secret (`X-Hub-Signature-256`) and posts it to
  `/webhooks/whatsapp`. Signature checks, deduplication, the 24-hour window and template fallback
  are therefore all exercised locally and in tests.
- The adapter enforces a **window policy**. Before a free-form send, it checks
  `conversations.last_customer_message_at`. Outside the 24-hour window it sends a mapped utility
  template instead (for example `application_update`). If no template is mapped, it refuses and
  records a failed delivery.
- **Durable acceptance:** the webhook verifies the signature, sanitises the text and stores each event
  in `inbound_events`, where deduplication by message ID and processing status are separate fields.
  It returns 200 **only after that commit**, and 503 when storing fails, so Meta retries. Events are
  drained per conversation in arrival order, and stuck events are recovered. Outbound replies are
  recorded as delivery intents before sending, and retried (`docs/design/whatsapp.md`; review 01,
  finding 2).
- The `/dev` simulator routes exist only when `APP_ENV=local` **and** `WHATSAPP_MODE=simulator`. A
  tunnel set up for a real Meta number therefore cannot reach a route that signs payloads.
- WhatsApp users are identified by `(channel="whatsapp", external_id=<user_id or wa_id>)`. Meta's
  business-scoped `user_id` is preferred because `wa_id` can be missing. A phone number is optional
  and treated as PII.
- `WHATSAPP_MODE` selects the outbound adapter: `simulator` (the default) or `cloud`.

## Consequences

- Anyone can run the full WhatsApp flow locally without a Meta account.
- Real Meta use needs a public HTTPS callback, such as a tunnel or the AWS deployment, plus the
  four secrets in `.env.example`.
- Templates must be created and approved in Meta's WhatsApp Manager. The repository holds their
  definitions and a mapping; Phase 1 does not sync them to Meta.

## Alternatives considered

- **Twilio or AWS End User Messaging:** about $0.005 per message on top of Meta's fees, and another vendor.
- **Mocking at the HTTP-client level only:** that would leave webhook verification and parsing untested.
