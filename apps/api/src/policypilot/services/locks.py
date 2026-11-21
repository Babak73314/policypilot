"""The single conversation lock protocol.

See docs/design/agent-design.md, section "Concurrency: one lock protocol".

A session-level advisory lock on a dedicated connection checked out for the whole operation.
`pg_try_advisory_lock` never blocks; callers either fail fast (web: 409) or retry for a bounded
time (review resolution, human replies). If the process dies, the connection closes and
PostgreSQL releases the lock.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from policypilot.db.session import Database

_TRY = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_RELEASE = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


class ConversationBusyError(RuntimeError):
    """Another operation holds the conversation lock."""


def lock_key(conversation_id: uuid.UUID) -> str:
    return f"conv:{conversation_id}"


@asynccontextmanager
async def conversation_lock(
    db: Database, conversation_id: uuid.UUID, *, wait_seconds: float = 0.0
) -> AsyncIterator[AsyncConnection]:
    """Hold the conversation lock; raise ConversationBusyError if it cannot be taken in time."""
    key = lock_key(conversation_id)
    async with db.engine.connect() as connection:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            acquired = (await connection.execute(_TRY, {"key": key})).scalar_one()
            await connection.commit()
            if acquired:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise ConversationBusyError(str(conversation_id))
            await asyncio.sleep(0.25)
        try:
            yield connection
        finally:
            # Shielded so a cancelled task still releases the lock on this connection.
            await asyncio.shield(_release(connection, key))


async def _release(connection: AsyncConnection, key: str) -> None:
    try:
        await connection.execute(_RELEASE, {"key": key})
        await connection.commit()
    except Exception:  # closing the connection releases the lock anyway
        await connection.invalidate()
