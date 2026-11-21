"""LangGraph Postgres checkpointer on its own idle-friendly pool (agent-design.md#checkpointing)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from policypilot.config import Settings


@asynccontextmanager
async def open_checkpointer(
    settings: Settings,
) -> AsyncIterator[tuple[AsyncPostgresSaver, AsyncConnectionPool[AsyncConnection[DictRow]]]]:
    """Yield a saver backed by a pool with min_size=0 so idle connections close (shared Aurora).

    `langgraph-checkpoint-postgres` has no schema option; `search_path=langgraph` keeps its tables
    out of the `app` schema (ADR-0004).
    """
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=settings.psycopg_url,
        min_size=0,
        max_size=settings.checkpoint_pool_max,
        max_idle=60,
        timeout=20,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
            "options": "-c search_path=langgraph",
            "connect_timeout": 10,
        },
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool), pool
    finally:
        await pool.close()
