"""Async database engine and sessions (psycopg 3 for both SQLAlchemy and LangGraph).

Pools are idle-friendly so a shared Aurora Serverless cluster can pause (docs/deployment/aws.md):
SQLAlchemy uses a small QueuePool that recycles idle connections; nothing polls while idle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from policypilot.config import Settings


class Database:
    """Owns the engine and the session factory for one application instance."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=max(1, settings.db_pool_max // 2),
            max_overflow=max(0, settings.db_pool_max - settings.db_pool_max // 2),
            pool_pre_ping=True,
            pool_recycle=300,
            pool_timeout=20,
            connect_args={"connect_timeout": 20, "prepare_threshold": None},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
