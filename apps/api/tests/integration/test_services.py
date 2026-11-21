"""Lock protocol, model budget and erasure against a real database."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from tests.helpers import new_session, say

from policypilot.agents.checkpoint import open_checkpointer
from policypilot.agents.models import reserve_model_call
from policypilot.db.models import Conversation, Customer, Message
from policypilot.services.locks import ConversationBusyError, conversation_lock
from policypilot.services.privacy import erase_customer

pytestmark = pytest.mark.db


async def test_lock_is_exclusive_and_released_after_errors(prepared_db: Any) -> None:
    conversation_id = uuid.uuid4()
    async with conversation_lock(prepared_db, conversation_id):
        with pytest.raises(ConversationBusyError):
            async with conversation_lock(prepared_db, conversation_id):
                pass
    with pytest.raises(RuntimeError):
        async with conversation_lock(prepared_db, conversation_id):
            raise RuntimeError("boom")
    async with conversation_lock(prepared_db, conversation_id):
        pass  # released despite the exception


async def test_lock_is_released_when_the_task_is_cancelled(prepared_db: Any) -> None:
    conversation_id = uuid.uuid4()
    entered = asyncio.Event()

    async def hold() -> None:
        async with conversation_lock(prepared_db, conversation_id):
            entered.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(hold())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with conversation_lock(prepared_db, conversation_id, wait_seconds=2):
        pass


async def test_model_budget_is_reserved_atomically(prepared_db: Any) -> None:
    async with prepared_db.transaction() as session:
        await session.execute(text("DELETE FROM app.llm_usage_counters"))
    results = await asyncio.gather(*[reserve_model_call(prepared_db, 3) for _ in range(6)])
    assert sorted(results) == [False, False, False, True, True, True]


async def test_erasure_removes_pii_checkpoints_and_tokens(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    conversation_id, headers = await new_session(client)
    await say(client, conversation_id, headers, "I'm 40 and want life cover, email me at j@x.test")
    async with prepared_db.session() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
        customer_id = conversation.customer_id

    from datetime import UTC, datetime

    from policypilot.db.models import OutboxEvent
    from policypilot.services.events import enqueue_event

    async with prepared_db.transaction() as session:
        data = {"leadId": "l-1", "customerDisplayName": "Jo", "customerId": str(customer_id)}
        pending = await enqueue_event(session, "lead.created", data, key=f"erase-p:{customer_id}")
        delivered = await enqueue_event(session, "lead.created", data, key=f"erase-d:{customer_id}")
        other = await enqueue_event(
            session,
            "lead.created",
            {**data, "customerId": str(uuid.uuid4())},
            key=f"o:{customer_id}",
        )
        (await session.get(OutboxEvent, delivered)).delivered_at = datetime.now(UTC)

    await erase_customer(prepared_db, settings, customer_id)

    async with prepared_db.session() as session:
        assert await session.get(OutboxEvent, pending) is None  # never sent
        assert (await session.get(OutboxEvent, delivered)).payload == {"erased": True}
        assert (await session.get(OutboxEvent, other)).payload["data"][
            "customerDisplayName"
        ] == "Jo"

    async with prepared_db.session() as session:
        contents = (
            (
                await session.execute(
                    select(Message.content).where(
                        Message.conversation_id == uuid.UUID(conversation_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        customer = await session.get(Customer, customer_id)
    assert set(contents) == {"[erased]"}
    assert customer.erased_at is not None
    async with open_checkpointer(settings) as (saver, _pool):
        assert await saver.aget_tuple({"configurable": {"thread_id": conversation_id}}) is None
    response = await client.get(
        f"/api/v1/chat/sessions/{conversation_id}/messages", headers=headers
    )
    assert response.status_code == 401
