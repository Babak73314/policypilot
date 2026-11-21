# ADR-0001: Monorepo with uv (Python) and npm (web)

- Status: Accepted
- Date: 2026-11-20

## Context

PolicyPilot has a Python API (FastAPI, LangGraph, MCP server), a React web app,
n8n workflow exports, seed data and docs. A reviewer should be able to clone one
repository and run everything with one command.

## Decision

- Use one repository with this layout: `apps/api` (Python 3.12), `apps/web` (React and TypeScript),
  `workflows/n8n`, `data/seed`, `docs`. `docker-compose.yml` and a `Makefile` sit at the root.
  The full layout is in [development.md](../development.md).
- Python uses **uv** for dependencies and lockfiles (`apps/api/uv.lock`), ruff for linting and
  formatting, mypy in strict mode, and pytest.
- The web app uses **npm** with a committed `package-lock.json`, ESLint, the TypeScript compiler
  for type checks, and Vitest.
- Commits follow Conventional Commits. CI runs lint, type checks, tests, the offline eval suite and a
  compose smoke test.
- Exact versions are pinned in lockfiles. `docs/reference/verified-facts.md` explains each pin.

## Consequences

- Each app has its own native toolchain, with no polyglot build tool to learn.
- The `Makefile` gives contributors one entry point (`make up`, `make test`, `make eval`).
  On Windows without `make`, every target is a single command documented in `development.md`.

## Alternatives considered

- **Poetry or pip-tools:** slower and less common for new projects in 2026 than uv.
- **pnpm or Nx workspaces:** with a single web app there is nothing for them to share.
- **Separate repositories:** a reviewer would have to clone and wire several repositories.
