# Local development

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker Desktop (with WSL 2 on Windows) or Docker Engine, with Compose v2 | recent | The quick start; everything below runs in containers |
| Python | 3.12 | Running the API outside Docker |
| uv | 0.13+ | Python dependencies |
| Node.js | 24 LTS | The web app outside Docker |
| make | any | Optional. Each target is also shown as a plain command |

You need no API keys. Without `ANTHROPIC_API_KEY`, the assistant runs on the scripted demo model
(ADR-0017), and the chat shows a "demo mode" banner.

## Quick start

```bash
python scripts/init_env.py    # creates .env with random local secrets; refuses to overwrite
docker compose up --build -d  # first build takes a few minutes (embedding model download)
python scripts/smoke.py       # optional: checks the whole path, including n8n and Mailpit
```

If Python is not installed on the host, run `docker compose run --rm init-env` instead. It runs
the same script in a container and writes `.env` into the repository folder. The API refuses
to start if `.env` still holds placeholder secrets.

Then open:

| URL | What |
|---|---|
| http://localhost:8080 | Customer chat |
| http://localhost:8080/simulator | WhatsApp simulator |
| http://localhost:8080/agent | Agent dashboard. Log in as `agent@policypilot.test` with `SEED_AGENT_PASSWORD` from `.env` |
| http://localhost:8080/api/v1/docs | OpenAPI docs |
| http://localhost:5678 | n8n. Create the owner account on first visit |
| http://localhost:8025 | Mailpit (emails sent by n8n) |

To use Claude, set `ANTHROPIC_API_KEY` in `.env` and run `docker compose up -d api`. To enable the
OpenAI fallback, set `OPENAI_API_KEY` and `LLM_FALLBACK_PROVIDER=openai`.

## Compose services

| Service | Image | Role |
|---|---|---|
| `init-env` | `python:3.12-slim` (profile `init`) | Runs `scripts/init_env.py` for hosts without Python. One-shot. |
| `db` | `pgvector/pgvector:0.8.7-pg16-trixie` | PostgreSQL 16. `docker/postgres/init.sh` creates the `policypilot` and `n8n` databases, their roles and the `vector` extension. |
| `migrate` | API image | `policypilot db upgrade`: Alembic, then the LangGraph checkpointer setup. One-shot. |
| `seed` | API image | `policypilot seed`: carriers, products, rate tables and templates; renders and ingests the policy PDFs; creates the demo agent and local API tokens. Idempotent and one-shot. |
| `api` | `apps/api/Dockerfile` | FastAPI on port 8000 (also published on `127.0.0.1:8000` for the API reference and the MCP smoke test) |
| `web` | `apps/web/Dockerfile` | nginx serving the build and proxying API paths (routing matrix in api.md). Port 8080. |
| `n8n-import` | `n8nio/n8n:2.48.6` | Imports credentials and workflows and publishes them **before** `n8n` starts (n8n.md). One-shot. |
| `n8n` | `n8nio/n8n:2.48.6` | Port 5678. Uses the `n8n` database. Starts after `n8n-import` succeeds. |
| `mailpit` | `axllent/mailpit:v1.34` | SMTP on 1025 (internal), web UI on 8025 |
| `webhook-gateway` | `nginxinc/nginx-unprivileged:1.30-alpine` (only with `-f docker-compose.tunnel.yml`) | Port 8081, forwards only `/webhooks/whatsapp`, for a tunnel to a real Meta test number (whatsapp.md) |

All published ports are bound to `127.0.0.1`, so nothing is reachable from the network by default.
The API image is about 1.8 GB: onnxruntime, the baked-in embedding model and the LangChain stack.
`migrate`, `seed` and `api` share it, and only `migrate` builds it.
The routing matrix for compose, Vite and AWS is in [api.md](design/api.md#routing-matrix).

`docker compose down -v` resets everything.

## Make targets

The `Makefile` is a shortcut for Linux, macOS, WSL and CI; each target is a plain command, so
Windows users run the command directly. `make help` lists the targets.

| Target | Command it runs |
|---|---|
| `make env` | `python3 scripts/init_env.py` |
| `make up` / `make down` | `docker compose up -d --build --wait` / `docker compose down` |
| `make logs` | `docker compose logs -f api` |
| `make test` | `make test-api test-web` |
| `make test-api` | `cd apps/api && uv run pytest` (needs `db` running: `docker compose up -d db`) |
| `make test-web` | `cd apps/web && npm test` |
| `make lint` | ruff (check and format), mypy, ESLint, tsc, Prettier |
| `make format` | ruff format and fixes, Prettier |
| `make eval` | `cd apps/api && uv run python -m evals` (offline; resets the `policypilot_test` database) |
| `make eval-live` | The same with `--live`: Claude and an LLM judge. Needs `ANTHROPIC_API_KEY`, costs about $1-2 |
| `make smoke` | `python3 scripts/smoke.py`, end to end through `http://127.0.0.1:8080` |
| `make mcp-smoke` | The MCP SDK client against the running stack |
| `make seed` / `make reindex` | `docker compose run --rm seed` / the same with `policypilot seed --reindex` (re-embeds every document) |
| `make n8n-export` | `python3 scripts/n8n_export.py`: exports the `pp-*` workflows into `workflows/n8n/`, strips instance fields and static data, restores the placeholders and fails on credential data or local secrets |

## Running the API outside Docker

```bash
docker compose up -d db
cd apps/api
uv sync
uv run policypilot db upgrade
uv run policypilot seed
uv run uvicorn --factory policypilot.main:app_factory --reload --port 8000
```

Then run the web dev server with `cd apps/web && npm ci && npm run dev`. It serves on port 5173
and proxies `/api`, `/webhooks`, `/mcp`, `/dev`, `/healthz` and `/readyz` to `127.0.0.1:8000`
(`VITE_API_TARGET` overrides it).

## Monorepo layout

```text
policypilot/
├── apps/
│   ├── api/                        # Python 3.12, FastAPI, LangGraph, MCP
│   │   ├── pyproject.toml, uv.lock, Dockerfile, alembic.ini
│   │   ├── migrations/             # Alembic revisions
│   │   ├── src/policypilot/
│   │   │   ├── main.py             # app factory, lifespan (pools, MCP session manager, dispatcher)
│   │   │   ├── config.py           # pydantic-settings
│   │   │   ├── cli.py              # `policypilot` command: db, seed, tokens, customers, maintenance, eval
│   │   │   ├── api/                # routers: chat, agent, auth, catalog, integrations, health, dev
│   │   │   ├── agents/             # graph.py, state.py, nodes/, tools.py, prompts/, llm/ (factory, fake)
│   │   │   ├── domain/             # models (SQLAlchemy), schemas (Pydantic), rating/, services/
│   │   │   ├── rag/                # pdf parsing, chunking, embeddings, retrieval
│   │   │   ├── channels/           # base, web, whatsapp/ (webhook, payloads, cloud, simulator, window)
│   │   │   ├── mcp_server/         # server factory, auth (TokenVerifier)
│   │   │   ├── events/             # outbox writer, dispatcher
│   │   │   ├── observability/      # logging, run tracer, pricing, callbacks extension point
│   │   │   ├── security/           # pii detectors, auth (jwt, passwords, api tokens), rate limits
│   │   │   └── i18n/               # locale helpers, Babel formatting, template lookup
│   │   ├── tests/                  # unit/, graph/, integration/, contract/
│   │   └── evals/                  # scenarios/*.yaml, runner, reports/ (git-ignored)
│   └── web/                        # React 19 + Vite + TS
│       ├── src/
│       │   ├── app/                # routes, providers, i18n setup
│       │   ├── chat/               # chat page, SSE client, quote card, comparison table
│       │   ├── simulator/          # WhatsApp simulator page
│       │   ├── agent/              # dashboard: login, reviews, conversations + trace, leads, catalog
│       │   ├── lib/                # api client, money/date formatting
│       │   └── locales/en/         # i18next resources
│       └── Dockerfile, nginx.conf
├── data/seed/                      # carriers, products, rate tables, policy wording sources, templates
├── workflows/n8n/                  # exported workflow JSON
├── docker/                         # postgres init, n8n import script
├── scripts/                        # smoke tests, helpers
├── docs/                           # this documentation
├── docker-compose.yml
├── .env.example
├── Makefile
└── .github/workflows/ci.yml
```

## Conventions

- **Commits:** Conventional Commits (`feat(api): …`, `fix(web): …`, `docs: …`), small and focused.
- **Python:** ruff (lint and format, line length 100), mypy strict, no `Any` in public signatures,
  async everywhere in the request path.
- **TypeScript:** strict mode, ESLint (typescript-eslint, react-hooks, i18next literal-string rule).
- **No secrets** in code, fixtures or seed data. Fictional names only. Email domains use `.test` or
  `example.com`.
- **User-facing strings:** i18n resources (web) and `message_templates` (channels), never inline
  (ADR-0013).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `migrate` exits with "refusing to migrate database …" | `DATABASE_URL` points to the wrong database name. The guard expects `policypilot`. |
| Chat shows "demo mode" | `ANTHROPIC_API_KEY` is empty. This is expected without a key. |
| WhatsApp simulator gets `401 invalid_signature` | `WHATSAPP_APP_SECRET` changed after the API started. Restart the `api` service. |
| n8n workflows inactive | Re-run `docker compose run --rm n8n-import`. |
| Port 5432 in use | Change `DB_PORT` in `.env`. |
