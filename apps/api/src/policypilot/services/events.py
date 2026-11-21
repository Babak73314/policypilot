"""Outbox events and the audit log, written inside the caller's transaction (ADR-0012)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import AuditLog, OutboxEvent

EVENT_NAMESPACE = uuid.UUID("2f0c8a8e-4d1c-4b7a-9a52-6a3f3c1f9b10")
EVENT_TYPES = frozenset(
    {
        "lead.created",
        "application.submitted",
        "application.reviewed",
        "handoff.requested",
        "handoff.resolved",
    }
)

# Called after commit to wake the in-process dispatcher (set by the app at start-up).
_nudge: list[Any] = []


def on_event_committed(callback: Any) -> None:
    _nudge.clear()
    _nudge.append(callback)


def nudge_dispatcher() -> None:
    for callback in _nudge:
        callback()


def event_id(key: str) -> uuid.UUID:
    """Deterministic event id, so replaying a graph node cannot emit the same event twice."""
    return uuid.uuid5(EVENT_NAMESPACE, key)


async def enqueue_event(
    session: AsyncSession,
    event_type: str,
    data: dict[str, Any],
    *,
    key: str | None = None,
    links: dict[str, str] | None = None,
) -> uuid.UUID:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type}")
    identifier = event_id(key) if key else uuid.uuid4()
    payload = {
        "id": str(identifier),
        "type": event_type,
        "occurredAt": datetime.now(UTC).isoformat(),
        "data": data,
        "links": links or {},
    }
    await session.execute(
        insert(OutboxEvent)
        .values(id=identifier, event_type=event_type, payload=payload)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return identifier


def audit(
    session: AsyncSession,
    *,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    actor_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )
