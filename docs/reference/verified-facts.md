# Verified facts (checked 2026-11-20)

The design docs and ADRs depend on the external facts below. Each one was
checked against an official source on the date above. Versions, prices and
policies change: re-check a fact before you rely on it, and update this file
when you do. Pinned versions live in `pyproject.toml`, `package.json` and
`docker-compose.yml`. This page explains why those pins were chosen.

## Claude API

Sources: the Anthropic models overview and pricing pages (platform.claude.com) and the
`langchain-anthropic` source.

| Model | ID | Input / output per 1M tokens | Notes |
|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | $4 / $20 (cache read $0.20) | Thinking is always on, and `{type: "disabled"}` returns 400. Effort defaults to `medium`. Forced `tool_choice` (`any`/`tool`) returns 400. |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 (cache read $0.10) | Forced `tool_choice` returns 400. Effort defaults to `high`. |
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 (prompts up to 100K tokens) | Has no server-side refusal fallback. |
| Claude Fable 5.2 | `claude-fable-5-2` | $8 / $40 | Not used here. |

- Every model above has a 1M-token context window and accepts up to 128K output tokens.
- Use structured outputs through `output_config.format`. The older `output_format` is deprecated.
- To get schema-valid tool arguments, set `strict: true` on the tool definition, not forced tool choice.
- Citations (`citations: {enabled: true}` on document blocks) cannot be combined with
  `output_config.format`, which returns 400.
- Assistant-message prefill returns 400 on all the models above.
- Opus 5.5 and Sonnet 5.5 have a server-side refusal fallback. It is enabled with beta
  `server-side-fallback-2026-07-01` and `fallbacks: "default"`.
- Since API version `2026-10-21`, sending `temperature`, `top_p` or `top_k` returns 400.
- The report's claim that forced tool use returns 400 on Sonnet/Opus 5.5 is **correct**.

## Python packages (PyPI, 2026-11-20)

| Package | Version | What matters here |
|---|---|---|
| fastapi | 0.165.0 | It has had built-in SSE since 0.135.0 (`from fastapi.sse import EventSourceResponse, ServerSentEvent`), with an automatic 15 s keep-alive. |
| langgraph | 1.2.23 | Pause with `from langgraph.types import interrupt, Command` and `interrupt(value)`, then resume with `Command(resume=...)` on the same `thread_id`. Pending interrupts appear in `result["__interrupt__"]` and in `StateSnapshot.interrupts`. State can be a TypedDict (preferred), a dataclass or a Pydantic model. `astream(stream_mode=[...])` supports `messages`, `updates` and `custom` (via `get_stream_writer`). |
| langgraph-checkpoint-postgres | 3.2.1 | `AsyncPostgresSaver.from_conn_string(uri)` followed by `await cp.setup()` creates the `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations` tables. It has **no schema parameter**, so a dedicated schema needs `search_path` on the connection. A custom pool needs `autocommit=True`, `row_factory=dict_row` and `prepare_threshold=0`. |
| langchain-core | 1.8.4 | `Runnable.with_fallbacks([...])` is still supported. |
| langchain-anthropic | 1.7.12 | **Always** call `with_structured_output(schema, method="json_schema")`. It uses native `output_config.format`. The default `function_calling` method drops forced tool choice on Opus/Sonnet 5.5 and can raise when the model does not call the tool. `bind_tools(..., strict=True)` is supported. `bind_tools(tool_choice="any")` is **not** guarded and returns 400 on Opus/Sonnet 5.5. Other parameters: `betas=[...]`, `model_kwargs={...}`, `effort=` and `cache_control` on content blocks. |
| langchain-aws | 1.9.3 (not a Phase 1 dependency) | Provides `ChatAnthropicBedrock` (`langchain-aws[anthropic]`), a ChatAnthropic subclass for Claude on Bedrock, and `ChatBedrockConverse`. The Bedrock fallback is deferred (ADR-0007). |
| langchain-openai | 1.8.5 | Fallback provider (see ADR-0006). |
| anthropic | 2.11.0 | Pulled in through langchain-anthropic, which needs `>=0.120.0`. |
| mcp | 2.3.0 (2026-10-02) | `FastMCP` was renamed to `MCPServer` (`from mcp.server import MCPServer`). Protocol revision 2026-07-28 is stateless: no initialize handshake and no `Mcp-Session-Id`. The SDK serves both 2025-11-25 and 2026-07-28. Streamable HTTP is the standard transport. Mount it with `streamable_http_app(...)`; the host lifespan must enter `mcp.session_manager.run()`. Auth works through a `TokenVerifier` plus `AuthSettings`, acting as an OAuth resource server with RFC 9728 metadata. There is no built-in static-bearer mode, so write a custom verifier. |
| langfuse | 5.2.0 (not a Phase 1 dependency) | `from langfuse.langchain import CallbackHandler`. Configure it with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL`. Masking is done with `mask_otel_spans`. SDK v5 needs a **Langfuse v4 server (>= 4.59)**. Deferred to Phase 2 (ADR-0011). |
| pgvector (Python) | 0.5.1 | `from pgvector.sqlalchemy import VECTOR`. Create HNSW indexes with `vector_cosine_ops`. |
| fastembed | 0.9.1 | The default model is `BAAI/bge-small-en-v1.5` (384 dimensions). It runs on CPU through ONNX Runtime and needs no API key. |
| psycopg / psycopg-pool | 3.3.9 / 3.3.3 | |
| sqlalchemy / alembic | 2.1.9 / 1.20.1 | |
| pydantic / pydantic-settings | 2.14.4 / 2.16.3 | |
| uvicorn | 0.54.0 | |
| structlog | 26.1.0 | |
| pytest / pytest-asyncio | 9.2.0 / 1.5.0 | |
| ruff / mypy | 0.17.6 / 2.4.1 | |
| uv | 0.13.9 | |
| pypdf | 7.1.1 | |
| fpdf2 | 2.9.0 | Used to generate the synthetic policy PDFs. |

## Frontend packages (npm, 2026-11-20)

react 19.3.1, vite 8.3.8, @vitejs/plugin-react 6.3.0, typescript 7.0.2,
@tanstack/react-query 5.108.1, react-router 8.5.2, i18next 27.2.2,
react-i18next 18.0.3, tailwindcss 4.3.5 (with @tailwindcss/vite), vitest 5.1.1,
@testing-library/react 16.3.3, jsdom 31.0.1, eslint 10.15.0,
typescript-eslint 8.73.0, @microsoft/fetch-event-source 2.0.1, msw 3.0.7.

## Container images

| Image | Tag used | Note |
|---|---|---|
| `pgvector/pgvector` | `0.8.7-pg16-trixie` | PostgreSQL 16 matches the planned Aurora PostgreSQL 16 target (Aurora 16.x ships pgvector 0.8.x); see `docs/deployment/aws.md`. |
| `n8nio/n8n` | `2.48.6` | The latest stable release. n8n 2.x replaced active/inactive with publish/unpublish. Import with `n8n import:workflow --separate --input=<dir>`; imported workflows are unpublished until `n8n publish:workflow --id=<id>`. Production webhooks are served at `/webhook/<path>` and test webhooks at `/webhook-test/<path>`. The public URL is set with `N8N_WEBHOOK_URL` (`WEBHOOK_URL` has been deprecated since 2.35). For Postgres, set `DB_TYPE=postgresdb` and `DB_POSTGRESDB_SCHEMA`. Checked by running it: `import:credentials` encrypts plain `data` objects with `N8N_ENCRYPTION_KEY`, custom IDs such as `pp-events-router` are accepted, and with PostgreSQL 16 it logs that 16 "receives compatibility support only" (17 or newer recommended). License: Sustainable Use License (internal, personal or non-commercial use only). |
| `axllent/mailpit` | `v1.34` | Catches the n8n emails locally. SMTP on `1025` (compose network only), web UI and REST API (`/api/v1/messages`, `/api/v1/search`) on `8025`. Messages are kept in memory, so a restart clears them. |
| `python` | `3.12-slim` | Base of the API image. |
| `ghcr.io/astral-sh/uv` | `0.13.9` | Copied into the API image for `uv sync --frozen --no-dev`. |
| `node` | `24-alpine` | Build stage of the web image (Node 24.22.3 when checked). |
| `nginxinc/nginx-unprivileged` | `1.30-alpine` | Serves the web build and proxies the API (nginx 1.30.6 when checked); runs as a non-root user on port 8080. Also the webhook-only tunnel gateway. |
| `langfuse/langfuse` | not used | Self-hosting v4 needs Postgres, ClickHouse, Redis and S3/MinIO, and the docs recommend at least 4 cores and 16 GiB RAM. It is **not** in this repository's compose file, and Langfuse export is deferred to Phase 2 (ADR-0011). |

## WhatsApp Business Platform (Cloud API)

Sources: developers.facebook.com/documentation/business-messaging/whatsapp/*,
whatsappbusiness.com/policy.

- **Graph API version:** the latest is v27.0 (released 2026-11-04). Keep it as a
  configuration value, because Meta pages still show older versions. Messages are sent with
  `POST https://graph.facebook.com/v27.0/{phone-number-id}/messages`. The On-Premises API is retired.
- **Webhook verification:** Meta sends `GET ?hub.mode=subscribe&hub.verify_token=<x>&hub.challenge=<y>`.
  Reply `200` with the `hub.challenge` value as the body if the token matches, and a non-200 status
  otherwise. The callback must use valid TLS; self-signed certificates are rejected.
- **Webhook signing:** the header is `X-Hub-Signature-256: sha256=<hex>`, an HMAC-SHA256 keyed with the
  **app secret**. Compute it over the raw request body before parsing.
- **Retries:** failed deliveries are retried for up to **7 days**, so deduplicate on the message `id`. A
  batch can hold up to 1,000 updates.
- **Inbound message shape:** `entry[].changes[].value` contains `metadata.phone_number_id`,
  `contacts[].{profile.name, wa_id, user_id}` and `messages[].{from, from_user_id, id, timestamp, type, text.body}`.
  **New in 2026:** `from` and `wa_id` can be missing. Meta says to identify users by the
  business-scoped user ID (`user_id` / `from_user_id`). Status updates arrive in `value.statuses[]`.
- **24-hour customer service window:** the window opens, and resets, with each user message. Inside it
  you can send any message type. Once it closes, you can only send approved templates. Template
  categories are marketing, utility and authentication. A factual payment-due or renewal notice is
  likely utility; adding an offer or discount makes it marketing (this is inferred from Meta's examples).
- **Pricing:** per-message pricing started on 2025-07-01. Since **2026-10-01**, each business phone
  number gets 1,000 free delivered service messages per month, after which they are charged. Utility
  templates sent inside an open window have also been charged since that date.
- **AI Providers policy:** the policy was dated 2026-01-15 and takes effect for existing users on
  2026-02-16. It bans general-purpose AI assistants where AI is the *primary* functionality. Meta states
  that businesses using AI for their own customer service are not affected, so a task-specific insurance
  assistant is allowed.
- **Messaging Policy, section 3:** do not ask people to share full payment card numbers, financial
  account numbers, ID card numbers or other sensitive identifiers. The same section restricts requesting
  health information "if applicable". You must provide a "prompt, clear, and direct escalation path" to a
  human. Insurance is neither a prohibited nor a regulated vertical.
- **Limits:** a text body can be up to 4,096 characters. A message can have up to 3 reply buttons, each
  title up to 20 characters, and an interactive body up to 1,024 characters.
- **Test number:** Meta creates a test business number automatically. The "up to 5 recipient numbers"
  limit is **unverified** on current pages; check the App Dashboard.

## EU AI Act

Sources: eur-lex.europa.eu (Regulation (EU) 2024/1689 and Regulation (EU) 2026/1744).

- **Annex III 5(c), high-risk:** "AI systems intended to be used for risk assessment and pricing in
  relation to natural persons in the case of life and health insurance". *How PolicyPilot is
  classified is a design hypothesis, not a verified fact; see `docs/compliance.md`.*
- **Digital Omnibus, Regulation (EU) 2026/1744:** in force since 2026-07-27. It moves the Annex III
  high-risk obligations to **2027-12-02** and the Annex I obligations to 2028-08-02, both as fixed dates.
- **Article 50(1), transparency for chatbots:** applies from 2026-08-02. Users must be told they are
  talking to an AI system. PolicyPilot discloses this in the first message on both channels.

## Research report corrections

The original research report (a private Persian proposal, not in this repo) was
mostly right. These are the corrections and additions:

1. The WhatsApp Graph API is at v27.0 and must be configurable.
2. The 1,000 free service messages are counted **per phone number**, and in-window utility templates are charged too.
3. WhatsApp user identifiers can now be the business-scoped `user_id` with no phone number. The data model keys on a channel-scoped user ID, not on a phone number.
4. Self-hosting Langfuse v4 is much heavier than the report assumed, so Langfuse is not part of Phase 1 (ADR-0011).
5. Claude on Bedrock needs per-account model access, which the planned target account did not have when this was written, so the fallback provider is OpenAI (ADR-0007).
6. `langgraph-checkpoint-postgres` has no schema option, so schema isolation uses `search_path` (ADR-0004).
7. FastAPI has built-in SSE, so `sse-starlette` is not needed.


## GitHub Actions (CI)

Checked on 2026-11-20 against the GitHub releases API.

| Action | Reference used | Note |
|---|---|---|
| `actions/checkout` | `v7` | Latest release v7.0.1. |
| `actions/setup-node` | `v7` | Latest release v7.1.0. |
| `actions/upload-artifact` | `v7` | Latest release v7.0.2. |
| `docker/setup-buildx-action` | `v4` | Latest release v4.4.1. |
| `gitleaks/gitleaks-action` | `v3` | Latest release v3.0.0. Reads `.gitleaksignore` and inline `gitleaks:allow` comments. |
| `astral-sh/setup-uv` | `v10.3.3` | **No moving major tag** (`@v10` does not resolve), so an exact tag is pinned. |
