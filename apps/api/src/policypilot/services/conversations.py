"""Conversations, messages and turns (data-model.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import ChannelIdentity, Conversation, Customer, Message, Turn
from policypilot.security.ingress import SanitizedText


def message_view(message: Message) -> dict[str, Any]:
    meta = message.metadata_ or {}
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "createdAt": message.created_at.isoformat(),
        "citations": meta.get("citations", []),
        "artifacts": meta.get("artifacts", []),
        "actions": meta.get("actions", []),
    }


async def create_web_conversation(
    session: AsyncSession, *, locale: str, disclosure: str
) -> Conversation:
    customer = Customer(locale=locale)
    session.add(customer)
    await session.flush()
    conversation = Conversation(
        customer_id=customer.id, channel="web", locale=locale, ai_disclosed_at=datetime.now(UTC)
    )
    session.add(conversation)
    await session.flush()
    session.add(
        ChannelIdentity(customer_id=customer.id, channel="web", external_id=str(conversation.id))
    )
    session.add(Message(conversation_id=conversation.id, role="assistant", content=disclosure))
    return conversation


async def get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


MESSAGE_PAGE = 200


async def list_messages(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    after: int | None = None,
    limit: int = MESSAGE_PAGE,
) -> list[Message]:
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if after is not None:
        stmt = stmt.where(Message.id > after)
    return list((await session.execute(stmt.order_by(Message.id).limit(limit))).scalars())


async def find_turn(
    session: AsyncSession, conversation_id: uuid.UUID, client_message_id: str
) -> Turn | None:
    return (
        await session.execute(
            select(Turn).where(
                Turn.conversation_id == conversation_id,
                Turn.client_message_id == client_message_id,
            )
        )
    ).scalar_one_or_none()


async def register_turn(
    session: AsyncSession,
    conversation: Conversation,
    client_message_id: str,
    text: SanitizedText,
    *,
    existing: Turn | None,
) -> tuple[Turn, Message]:
    """Insert (or restart a failed) turn and store the sanitised customer message."""
    now = datetime.now(UTC)
    message = Message(
        conversation_id=conversation.id,
        role="customer",
        content=text.stored_text,
        redactions=text.redactions,
    )
    session.add(message)
    await session.flush()
    if existing is None:
        turn = Turn(
            conversation_id=conversation.id,
            client_message_id=client_message_id,
            customer_message_id=message.id,
        )
        session.add(turn)
    else:
        turn = existing
        turn.status = "running"
        turn.error_code = None
        turn.customer_message_id = message.id
        turn.finished_at = None
    message.turn_id = turn.id if turn.id else None
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(last_customer_message_at=now, last_activity_at=now)
    )
    await session.flush()
    message.turn_id = turn.id
    return turn, message


async def store_assistant_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID | None,
    content: str,
    metadata: dict[str, Any],
    *,
    role: str = "assistant",
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        metadata_=metadata,
        turn_id=turn_id,
    )
    session.add(message)
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(last_activity_at=datetime.now(UTC))
    )
    await session.flush()
    return message
