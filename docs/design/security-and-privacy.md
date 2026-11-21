# Security, guardrails and PII

## Threat model

| Asset | Threats | Main controls |
|---|---|---|
| Customer PII (names, contact details, needs profile) | Leaks through logs, traces, prompts, checkpoints or n8n events; access by other customers | One ingress sanitisation step; contact details never reach the model; conversation-scoped tokens with revocation; IDs-only events; agent auth; erasure inventory; audit log |
| Prices and advice | A hallucinated price or a cover promise | Deterministic engine (ADR-0003); server-rendered price placeholders; final deterministic validation before anything is shown; human approval of a frozen snapshot |
| Secrets | Commit to the public repository; logs | Generated `.env` (never committed); startup refuses placeholder secrets outside `local`; gitleaks in CI; Secrets Manager on AWS |
| Agent dashboard | Credential stuffing; IDOR | Argon2id; login rate limit; per-resource authorization; short-lived JWTs |
| Webhooks | Forged Meta calls; replay; signing bypass | HMAC verification; deduplication by provider message ID; 1 MB limit; `/dev` simulator routes mounted only in local simulator mode |
| MCP and integration APIs | Token theft; over-privilege; storage abuse | Hashed opaque tokens with scopes and expiry; per-token rate limits including quote creation; write audit |
| The LLM | Prompt injection (user text, documents); tool misuse; exfiltration | Tools only read the catalog or act on the customer's own conversation; strict tool schemas; delimited documents; output validation; no tool sends messages to arbitrary recipients or fetches URLs |
| Availability and cost | Flooding chat to run up the token bill | Rate limits including session creation; an atomic daily model-call budget; per-role token limits; a turn deadline; bounded concurrency per process |

## Ingress sanitisation (one step, before anything else)

`policypilot.security.ingress.sanitize(text, *, source)` runs **before** storage, logging,
tracing, checkpointing or any model call. It is applied to:

- customer messages (web, WhatsApp);
- agent replies and review notes;
- the text fields of WhatsApp webhooks;
- MCP free-text fields (`notes`);
- integration callback notes.

It returns `stored_text`, `model_text` and `redactions`:

| Detector | Detection | In `stored_text` | In `model_text` |
|---|---|---|---|
| Payment card | 13–19 digits (spaces and dashes allowed), not starting with 0, Luhn-valid | `[REDACTED:card]` | same |
| IBAN | Country length table and mod-97 check | `[REDACTED:iban]` | same |
| US SSN-like | `AAA-GG-SSSS` with invalid ranges excluded | `[REDACTED:national_id]` | same |
| National-ID-like | Next to keywords such as passport, ID number, driver's licence or SSN, and containing a digit | `[REDACTED:national_id]` | same |
| Volunteered health details | Conservative keyword and phrase list (conditions, diagnoses, medications, "I have …" with medical terms) | Sentence replaced by `[REDACTED:health]` | same |
| Email | RFC-like pattern | kept, so the dashboard can show contact details | `[EMAIL]` |
| Phone | 8 or more digits with separators | kept | `[PHONE]` |

Consequences:

- **The model never sees contact details or identifiers.** The application step parses name,
  email and phone from `stored_text` deterministically in the service layer and stores them on the
  customer or lead, never in graph state.
- **Health details are not processed.** The demo cannot reliably tell when health information is
  needed, so it does not keep any. A sentence that matches is removed before storage. The customer
  gets `health_data_notice`, which offers a private call with a licensed agent. The keyword list is
  imperfect; misses are a known limitation, recorded in compliance.md. The live eval suite has cases
  for this.
- **Every text source is filtered the same way**: customer messages, webhook text, agent replies,
  review notes, MCP lead notes and integration notes (review 02, finding 19).
- **Review decisions stay out of graph state.** A review note goes to the customer as
  `model_text` (contact details masked). The graph, and so its checkpoints, receives only the
  action, the masked note and the names of edited fields; clerical values and the reviewer's email
  stay on the review task row (review 02, finding 3).
- **The audit log holds no free text.** It is append-only and outlives erasure, so a note is
  recorded as `has_note`, never its text (review 02, finding 18).
- `redactions` holds only types and counts. Original values are never logged or stored.

## Prompt injection and tool safety

- User text is passed as user messages, never inserted into system prompts.
- Retrieved chunks are wrapped in `<document id=… product=… section=…>` tags. The system prompt says
  the content of those tags is reference material, not instructions.
- Tools cannot send messages, call URLs or touch other customers' data. Rating inputs come from the
  validated profile, not from free model arguments (agent-design.md). The one model-chosen input,
  a life coverage override for `get_quote`, is used only if the customer wrote that amount in the
  current message.
- Everything the model writes passes the **final deterministic validation**: prices only through
  `{{quote:<id>}}` placeholders, citations only to chunks retrieved this turn, banned claims,
  disclaimers and channel limits. If validation fails, a deterministic template is used.

## PII in logs, traces and events

- **Logs:** a structlog processor applies `mask_pii`, which masks identifiers, emails and phone
  numbers, to every string value. Request bodies and webhook bodies are never logged.
- **Run traces:** `agent_run_steps.summary` holds tool names, IDs, token counts and short masked
  text excerpts (200 characters at most). Raw prompts are never stored.
- **n8n events:** IDs, first names, product names, statuses and links only (ADR-0012).
- **Langfuse** export is deferred to Phase 2 (ADR-0011), so no third-party trace processor is
  used in Phase 1.

## Secrets

- `python scripts/init_env.py` creates `.env` from `.env.example` with random values for every
  secret: `APP_SECRET_KEY`, `SEED_AGENT_PASSWORD`, `MCP_DEV_TOKEN`, `N8N_INTEGRATION_TOKEN`,
  `N8N_WEBHOOK_TOKEN`, `N8N_ENCRYPTION_KEY`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` and the
  database passwords. It refuses to overwrite an existing `.env`. It uses only the standard library,
  so it runs on Windows, macOS and Linux. `docker compose run --rm init-env` is the alternative
  without host Python.
- The API refuses to start when a required secret is empty or still the `.env.example` placeholder
  (`change-me…`). This also applies in `local`.
- CI runs gitleaks. A test asserts that the seed and fixture files contain no key-like strings.
- On AWS, secrets come from Secrets Manager, with resource policies limited to the task role.

## Web security

- CORS allows only `WEB_ORIGINS`.
- nginx sends:
  - a CSP: `default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`;
  - `X-Content-Type-Options`;
  - `Referrer-Policy`;
  - `Permissions-Policy`.
- The customer token is in `localStorage`, with a 30-day sliding expiry, conversation scope and a
  server-side `token_version` for revocation. Agent tokens last 60 minutes and are kept in
  `sessionStorage`. Bearer tokens make CSRF irrelevant. The XSS exposure of `localStorage` is
  limited by the strict CSP, which allows no inline scripts and no third-party scripts.
- Every agent endpoint checks the role. Every customer endpoint checks that `sub` matches the
  conversation in the path and that `ver` equals `token_version`.
- Dependabot covers pip, npm, GitHub Actions and Docker.

## Abuse and cost limits

| Control | Default |
|---|---|
| Daily model-call budget (`LLM_DAILY_CALL_LIMIT`, atomic counter, reserved per attempt including retries, fallbacks, summaries and repairs) | 2,000 |
| Per-role output tokens | router and extractor 512, writer 800, advisor 2,048 |
| Advisor tool rounds and tool calls per turn | 4 and 8 |
| Turn deadline | 90 s |
| Concurrent turns per API process | 8 |
| Turns per conversation per day | 200 |
| Chat sessions created per IP address | 10 per hour |
| MCP quote-producing calls per token | 60 per hour |

When the model-call budget is exhausted, the conversation gets the `rate_limited` template and a
handoff.

## Incident basics

- The audit log records logins, review decisions, erasures, token creation and revocation, and MCP
  writes.
- `policypilot tokens revoke <prefix>` revokes an API token.
- Rotating `APP_SECRET_KEY` invalidates every JWT.
- Erasure increments `token_version` for the customer's conversations.
