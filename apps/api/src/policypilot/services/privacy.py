"""Data-subject requests: export and erasure across every store.

The inventory is in docs/design/data-model.md, section "Retention and erasure".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.config import Settings
from policypilot.db.models import (
    AgentRun,
    AgentRunStep,
    Application,
    ApplicationSnapshot,
    AuditLog,
    ChannelIdentity,
    Conversation,
    Customer,
    InboundEvent,
    Lead,
    Message,
    OutboundMessage,
    OutboxEvent,
    Quote,
    ReviewTask,
    SimulatorOutbox,
)
from policypilot.db.session import Database
from policypilot.services.locks import conversation_lock


class CustomerNotFoundError(LookupError):
    pass


def _row(obj: Any) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__mapper__.column_attrs}


async def export_customer(db: Database, customer_id: uuid.UUID) -> dict[str, Any]:
    """Everything stored about a customer, from the same inventory erasure uses."""
    async with db.session() as session:
        customer = await session.get(Customer, customer_id)
        if customer is None:
            raise CustomerNotFoundError(str(customer_id))
        conversations = (
            (
                await session.execute(
                    select(Conversation).where(Conversation.customer_id == customer_id)
                )
            )
            .scalars()
            .all()
        )
        conversation_ids = [c.id for c in conversations]

        async def rows(model: Any, *where: Any) -> list[dict[str, Any]]:
            return [_row(r) for r in (await session.execute(select(model).where(*where))).scalars()]

        applications = await rows(Application, Application.customer_id == customer_id)
        return {
            "customer": _row(customer),
            "channel_identities": await rows(
                ChannelIdentity, ChannelIdentity.customer_id == customer_id
            ),
            "conversations": [_row(c) for c in conversations],
            "messages": await rows(Message, Message.conversation_id.in_(conversation_ids)),
            "quotes": await rows(Quote, Quote.customer_id == customer_id),
            "leads": await rows(Lead, Lead.customer_id == customer_id),
            "applications": applications,
            "application_snapshots": await rows(
                ApplicationSnapshot,
                ApplicationSnapshot.application_id.in_([a["id"] for a in applications]),
            ),
            "review_tasks": await rows(
                ReviewTask, ReviewTask.conversation_id.in_(conversation_ids)
            ),
        }


async def _erase_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(
            status="closed",
            close_reason="erased",
            closed_at=now,
            token_version=Conversation.token_version + 1,
        )
    )
    await session.execute(
        update(Message)
        .where(Message.conversation_id == conversation_id)
        .values(content="[erased]", metadata_={}, redactions={})
    )
    for model in (InboundEvent, OutboundMessage, SimulatorOutbox):
        await session.execute(delete(model).where(model.conversation_id == conversation_id))
    await session.execute(
        update(ReviewTask)
        .where(ReviewTask.conversation_id == conversation_id)
        .values(summary="", payload={}, decision=None)
    )
    await session.execute(
        update(ReviewTask)
        .where(
            ReviewTask.conversation_id == conversation_id,
            ReviewTask.status.in_(("open", "claimed", "resolving")),
        )
        .values(status="resolved", resolution="withdrawn", resolved_at=now)
    )
    run_ids = select(AgentRun.id).where(AgentRun.conversation_id == conversation_id)
    await session.execute(
        update(AgentRunStep).where(AgentRunStep.run_id.in_(run_ids)).values(summary={})
    )


async def erase_customer(
    db: Database, settings: Settings, customer_id: uuid.UUID
) -> dict[str, int]:
    """Erase a customer. Each conversation is erased under its lock, then its checkpoints go."""
    from policypilot.agents.checkpoint import open_checkpointer

    async with db.session() as session:
        if await session.get(Customer, customer_id) is None:
            raise CustomerNotFoundError(str(customer_id))
        conversation_ids = list(
            (
                await session.execute(
                    select(Conversation.id).where(Conversation.customer_id == customer_id)
                )
            ).scalars()
        )

    async with open_checkpointer(settings) as (saver, _pool):
        for conversation_id in conversation_ids:
            async with conversation_lock(db, conversation_id, wait_seconds=30):
                async with db.transaction() as session:
                    await _erase_conversation(session, conversation_id)
                await saver.adelete_thread(str(conversation_id))

    now = datetime.now(UTC)
    async with db.transaction() as session:
        application_ids = select(Application.id).where(Application.customer_id == customer_id)
        await session.execute(
            update(ApplicationSnapshot)
            .where(ApplicationSnapshot.application_id.in_(application_ids))
            .values(data={}, needs_profile={})
        )
        await session.execute(
            update(Application)
            .where(
                Application.customer_id == customer_id,
                Application.status.in_(("draft", "pending_review", "changes_requested")),
            )
            .values(status="withdrawn")
        )
        await session.execute(
            update(Lead).where(Lead.customer_id == customer_id).values(profile={})
        )
        # Events are enveloped: {"id", "type", "occurredAt", "data": {...}, "links"}. Pending and
        # dead-lettered events are dropped (never sent); delivered ones keep only their type.
        customer_events = OutboxEvent.payload["data"]["customerId"].astext == str(customer_id)
        await session.execute(
            delete(OutboxEvent).where(OutboxEvent.delivered_at.is_(None), customer_events)
        )
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.delivered_at.is_not(None), customer_events)
            .values(payload={"erased": True})
        )
        await session.execute(
            delete(ChannelIdentity).where(ChannelIdentity.customer_id == customer_id)
        )
        await session.execute(
            update(Customer)
            .where(Customer.id == customer_id)
            .values(display_name=None, email=None, phone_e164=None, region=None, erased_at=now)
        )
        session.add(
            AuditLog(
                actor_type="system",
                action="customer.erased",
                entity_type="customer",
                entity_id=str(customer_id),
                details={"conversations": len(conversation_ids)},
            )
        )
    return {"conversations": len(conversation_ids)}
