# ADR-0014: Authentication for customers, agents and machine clients

- Status: Accepted
- Date: 2026-11-20

## Context

Three kinds of caller use PolicyPilot:

- anonymous **customers** on web chat (WhatsApp customers are identified by Meta);
- **human agents** using the dashboard;
- **machine clients**: MCP clients and n8n.

The report deferred proper identity (Cognito) to Phase 2. Phase 1 still needs real
access control, because the dashboard shows customer data.

## Decision

- **Customers (web):** `POST /api/v1/chat/sessions` creates a conversation and returns a
  **conversation token**, an HS256 JWT. Its claims are `sub` (conversation ID), `typ=customer` and
  `ver` (the conversation's `token_version`). It is signed with `APP_SECRET_KEY` and has a **30-day
  sliding expiry**: every response returns a renewed token. The browser keeps it in `localStorage`,
  so a customer can come back for a review decision after a restart. Pending reviews expire after
  14 days, well within the token lifetime. Erasure increments `token_version`, which revokes the
  token. A token grants access to its own conversation only (review 01, finding 21).
  Creating sessions is limited to 10 per hour per IP address.
- **Agents:** the `agent_users` table stores email, an Argon2id password hash (`argon2-cffi`) and a
  role (`agent` or `admin`). `POST /api/v1/auth/login` returns an access JWT (`typ=agent`, 60
  minutes). The dashboard keeps it in memory and in `sessionStorage`; there are no refresh tokens in
  Phase 1, so the agent logs in again. Login is rate-limited and failures go to the audit log. The
  seed creates one demo agent, whose password comes from `.env` (`SEED_AGENT_PASSWORD`, generated
  by `scripts/init_env.py`). The API refuses to start with placeholder secrets. Nothing is
  hard-coded.
- **Machine clients:** opaque API tokens of the form `pp_<random>`. Only their SHA-256 hash is
  stored, in `api_tokens`, along with scopes (`mcp:read`, `mcp:write`, `integrations`), an expiry and
  a last-used time. Tokens are created with `policypilot tokens create`, and the seed can register
  local tokens from `.env`.
- **WhatsApp webhook:** authenticated by the `X-Hub-Signature-256` HMAC (ADR-0009), with no bearer token.
- **CORS** is limited to `WEB_ORIGINS`. Every endpoint except health, the public catalog and the
  webhook requires a token.

## Consequences

- No external identity provider is needed to run the demo.
- Replacing agent login with Cognito or another OIDC provider in Phase 2 changes one dependency
  (`get_current_agent`). The dashboard already sends a bearer token.

## Alternatives considered

- **Cookie sessions with CSRF protection:** fine for the dashboard, but bearer tokens keep the API
  uniform across the web app, MCP and n8n.
- **Cognito now:** an AWS dependency for local development, which conflicts with the quick start.
