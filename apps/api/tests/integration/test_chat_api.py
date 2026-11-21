"""End-to-end chat turns through the API, the graph, the database and the fake model."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from tests.helpers import artifacts, completed, new_session, say

from policypilot.db.models import Message, Quote
from policypilot.services.locks import conversation_lock

pytestmark = pytest.mark.db


async def test_session_starts_with_ai_disclosure(client: Any) -> None:
    conversation_id, headers = await new_session(client)
    response = await client.get(
        f"/api/v1/chat/sessions/{conversation_id}/messages", headers=headers
    )
    body = response.json()
    assert body["status"] == "active"
    assert body["messages"][0]["role"] == "assistant"
    assert "AI assistant" in body["messages"][0]["content"]
    assert response.headers["X-Conversation-Token"]


async def test_needs_analysis_then_quotes_match_the_engine(client: Any, prepared_db: Any) -> None:
    conversation_id, headers = await new_session(client)
    status, events = await say(
        client, conversation_id, headers, "I'm 40 and I'd like life insurance"
    )
    assert status == 200
    question = completed(events)["text"].lower()
    assert "cover" in question or "years" in question

    status, events = await say(
        client, conversation_id, headers, "I want $500k of cover for 20 years"
    )
    assert status == 200
    cards = artifacts(events, "quote")
    premiums = {card["productName"]: card["premium"]["amountMinor"] for card in cards}
    # Hand-computed in tests/unit/rating/golden.yaml: Evergreen 7,300 and Kestrel 7,500 cents.
    assert premiums == {"Evergreen Term Life 20 Flex": 7300, "Kestrel Term Life 20": 7500}
    reply = completed(events)["text"]
    assert "$73.00 per month" in reply
    assert "$75.00 per month" in reply
    assert "{{quote:" not in reply
    assert "estimate" in reply.lower()

    async with prepared_db.session() as session:
        stored = (
            (
                await session.execute(
                    select(Quote).where(Quote.conversation_id == uuid.UUID(conversation_id))
                )
            )
            .scalars()
            .all()
        )
    assert {q.premium_minor for q in stored} == {7300, 7500}


async def test_policy_question_is_answered_with_a_citation(client: Any) -> None:
    conversation_id, headers = await new_session(client)
    status, events = await say(
        client, conversation_id, headers, "Is suicide covered under Kestrel Term Life 20?"
    )
    assert status == 200
    done = completed(events)
    assert done["citations"], done
    assert "Exclusions" in done["citations"][0]["section"]
    assert "[1]" in done["text"]
    assert "[doc:" not in done["text"]


async def test_out_of_scope_requests_are_declined(client: Any) -> None:
    conversation_id, headers = await new_session(client)
    _, events = await say(
        client, conversation_id, headers, "Can you write me a poem about the sea?"
    )
    assert "only help with insurance" in completed(events)["text"]


async def test_card_numbers_are_redacted_before_storage(client: Any, prepared_db: Any) -> None:
    conversation_id, headers = await new_session(client)
    _, events = await say(client, conversation_id, headers, "my card is 4111 1111 1111 1111")
    assert "removed what looked like a card" in completed(events)["text"]
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
    assert all("4111" not in content for content in contents)


async def test_retry_with_same_client_message_id_replays(client: Any) -> None:
    conversation_id, headers = await new_session(client)
    _, first = await say(
        client, conversation_id, headers, "hello", client_message_id="c-retry-0001"
    )
    _, second = await say(
        client, conversation_id, headers, "hello", client_message_id="c-retry-0001"
    )
    assert completed(second)["messageId"] == completed(first)["messageId"]
    assert completed(second)["replayed"] is True


async def test_busy_conversation_returns_409(client: Any, prepared_db: Any) -> None:
    conversation_id, headers = await new_session(client)
    async with conversation_lock(prepared_db, uuid.UUID(conversation_id)):
        status, _ = await say(client, conversation_id, headers, "hello")
    assert status == 409


async def test_tokens_are_scoped_to_their_conversation(client: Any) -> None:
    first_id, _ = await new_session(client)
    _, other_headers = await new_session(client)
    response = await client.get(f"/api/v1/chat/sessions/{first_id}/messages", headers=other_headers)
    assert response.status_code == 403
    response = await client.get(f"/api/v1/chat/sessions/{first_id}/messages")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_health_endpoints(client: Any) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["schema"] == "0001"
