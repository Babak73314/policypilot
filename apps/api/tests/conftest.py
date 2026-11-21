"""Shared fixtures.

Database tests run against the Docker Compose `db` service (database `policypilot_test`) or the
URL in TEST_DATABASE_URL. They are skipped when no database is reachable. No API keys are used:
the scripted fake model and the hashing embedder stand in (ADR-0017, ADR-0008).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]

if sys.platform == "win32":
    # psycopg's async mode cannot run on the Proactor loop; every test and fixture loop
    # (including session-scoped ones) is created from this policy. Linux CI is unaffected.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _dotenv(name: str) -> str | None:
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def test_database_url() -> str | None:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    password = _dotenv("POLICYPILOT_DB_PASSWORD")
    port = _dotenv("DB_PORT") or "5432"
    if not password:
        return None
    return f"postgresql+psycopg://policypilot:{password}@127.0.0.1:{port}/policypilot_test"


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    url = test_database_url()
    if url is None:
        pytest.skip("no test database (run scripts/init_env.py, then docker compose up -d db)")
    env = {
        "APP_ENV": "test",
        "DATABASE_URL": url,
        "POLICYPILOT_EXPECTED_DB": "policypilot_test",
        "APP_SECRET_KEY": "test-secret-key-0123456789abcdef",  # gitleaks:allow (test-only value)
        "LLM_PROVIDER": "fake",
        "EMBEDDING_PROVIDER": "hashing",
        "SEED_AGENT_PASSWORD": "test-agent-password",
        "MCP_DEV_TOKEN": "pp_test-mcp-token",
        "N8N_INTEGRATION_TOKEN": "pp_test-n8n-token",
        "WHATSAPP_APP_SECRET": "test-whatsapp-app-secret",
        "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
        "N8N_EVENTS_URL": "",
        "DOCUMENTS_OUTPUT_DIR": str(tmp_path_factory.mktemp("documents")),
    }
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from policypilot.config import Settings, get_settings

    get_settings.cache_clear()
    yield Settings()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest.fixture(scope="session")
async def prepared_db(settings: Any) -> AsyncIterator[Any]:
    """A freshly migrated and seeded test database, shared by the whole session."""
    import psycopg

    from policypilot.agents.checkpoint import open_checkpointer
    from policypilot.db.session import Database
    from policypilot.rag.embeddings import HashingEmbedder
    from policypilot.seed.run import run_seed

    try:
        async with await psycopg.AsyncConnection.connect(
            settings.psycopg_url, autocommit=True, connect_timeout=3
        ) as conn:
            await conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
            await conn.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
    except psycopg.OperationalError as exc:
        pytest.skip(f"test database not reachable: {exc}")

    from alembic import command
    from alembic.config import Config

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    async with open_checkpointer(settings) as (saver, _pool):
        await saver.setup()

    db = Database(settings)
    await run_seed(db, settings, HashingEmbedder(settings.embedding_dimensions))
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
async def app(prepared_db: Any, settings: Any) -> AsyncIterator[Any]:
    from policypilot.main import create_app
    from policypilot.rag.embeddings import HashingEmbedder

    application = create_app(settings, embedder=HashingEmbedder(settings.embedding_dimensions))
    # The lifespan (including the MCP session manager's task group) must be entered and exited in
    # the same task, so it runs in a dedicated task for the whole session.
    started, stop = asyncio.Event(), asyncio.Event()
    failure: list[BaseException] = []

    async def serve() -> None:
        try:
            async with application.router.lifespan_context(application):
                started.set()
                await stop.wait()
        except BaseException as exc:
            failure.append(exc)
            started.set()
            raise

    task = asyncio.create_task(serve())
    await started.wait()
    if failure:
        raise failure[0]
    yield application
    stop.set()
    await task


@pytest.fixture
async def client(app: Any) -> AsyncIterator[Any]:
    import httpx

    from policypilot.api.state import RateLimiter

    app.state.policypilot.limiter = RateLimiter()  # each test starts with fresh rate limits
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
