# Shortcuts for Linux, macOS, WSL and CI. Every target is a plain command you can also run
# directly (the README lists them), so make is optional, including on Windows.
.PHONY: help env up down logs test test-api test-web lint format eval eval-live smoke mcp-smoke \
	seed reindex n8n-export

PYTHON ?= python3
API = cd apps/api &&
WEB = cd apps/web &&
PY_PATHS = src tests migrations evals ../../scripts

help:
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

env: ## Write .env with fresh local secrets (never overwrites)
	$(PYTHON) scripts/init_env.py

up: ## Build and start the stack, waiting until it is healthy
	docker compose up -d --build --wait

down: ## Stop the stack (docker compose down -v also deletes the data)
	docker compose down

logs: ## Follow the API logs
	docker compose logs -f api

test: test-api test-web ## All unit and integration tests

test-api: ## API tests (needs the db service: docker compose up -d db)
	$(API) uv run pytest

test-web: ## Web tests
	$(WEB) npm test

lint: ## ruff, mypy, ESLint, tsc and Prettier
	$(API) uv run ruff check $(PY_PATHS) && uv run ruff format --check $(PY_PATHS) && uv run mypy
	$(WEB) npm run lint && npm run typecheck && npm run format:check

format: ## Apply ruff and Prettier formatting
	$(API) uv run ruff format $(PY_PATHS) && uv run ruff check --fix $(PY_PATHS)
	$(WEB) npm run format

eval: ## Offline eval suite (scripted model; resets the policypilot_test database)
	$(API) uv run python -m evals

eval-live: ## Live eval suite with Claude and the judge (needs ANTHROPIC_API_KEY; about $1-2)
	$(API) uv run python -m evals --live

smoke: ## End-to-end smoke test against the running stack
	$(PYTHON) scripts/smoke.py

mcp-smoke: ## MCP SDK client against the running stack
	$(API) uv run python ../../scripts/mcp_smoke.py

seed: ## Re-run the (idempotent) seed in the stack
	docker compose run --rm seed

reindex: ## Re-embed every policy document in the stack
	docker compose run --rm seed policypilot seed --reindex

n8n-export: ## Export the n8n workflows into workflows/n8n/ (sanitised)
	$(PYTHON) scripts/n8n_export.py
