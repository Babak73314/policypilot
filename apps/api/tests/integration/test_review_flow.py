"""Applications, human review (interrupt and resume), handoff and the agent API, end to end."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.helpers import artifacts, completed, new_session, parse_sse, say

from policypilot.db.models import Application, Conversation, OutboxEvent, ReviewTask

pytestmark = pytest.mark.db


async def agent_headers(client: Any, settings: Any) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "agent@policypilot.test",
            "password": settings.seed_agent_password.get_secret_value(),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


async def act(
    client: Any, conversation_id: str, headers: dict[str, str], text: str, action: str
) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={"text": text, "clientMessageId": f"c-{uuid.uuid4()}", "action": action},
        headers=headers,
        timeout=60,
    )
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def submit_application(client: Any) -> tuple[str, dict[str, str]]:
    conversation_id, headers = await new_session(client)
    await say(client, conversation_id, headers, "I'm 40 and I'd like life insurance")
    _, events = await say(client, conversation_id, headers, "I want $500k of cover for 20 years")
    kestrel = next(
        c for c in artifacts(events, "quote") if c["productName"] == "Kestrel Term Life 20"
    )

    _, events = await say(client, conversation_id, headers, "I'd like to apply please")
    done = completed(events)
    assert {a["label"] for a in done["actions"]} >= {"Kestrel Term Life 20"}

    events = await act(
        client, conversation_id, headers, "Kestrel Term Life 20", f"choose:{kestrel['quoteId']}"
    )
    assert "please share your name" in completed(events)["text"]

    _, events = await say(
        client, conversation_id, headers, "My name is Jane Doe, email jane.doe@example.test"
    )
    assert "May a licensed agent" in completed(events)["text"]

    events = await act(client, conversation_id, headers, "Yes", "consent_yes")
    summary = completed(events)["text"]
    assert "$75.00 per month" in summary
    assert "j***@example.test" in summary
    assert "jane.doe" not in summary

    events = await act(client, conversation_id, headers, "Submit", "submit")
    done = completed(events)
    assert "sent to a licensed agent" in done["text"]
    assert done["conversationStatus"] == "awaiting_review"
    return conversation_id, headers


async def open_task(prepared_db: Any, conversation_id: str) -> ReviewTask:
    async with prepared_db.session() as session:
        return (
            (
                await session.execute(
                    select(ReviewTask)
                    .where(ReviewTask.conversation_id == uuid.UUID(conversation_id))
                    .order_by(ReviewTask.created_at.desc())
                )
            )
            .scalars()
            .first()
        )


async def test_application_review_and_approval(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    conversation_id, headers = await submit_application(client)
    task = await open_task(prepared_db, conversation_id)
    assert task.kind == "application_review"
    assert task.status == "open"

    # While paused: one acknowledgement, then silence (the agent sees the messages).
    _, events = await say(client, conversation_id, headers, "any news?")
    assert "reviewing your request" in completed(events)["text"]
    _, events = await say(client, conversation_id, headers, "still there?")
    assert completed(events)["messageId"] is None

    staff = await agent_headers(client, settings)
    queue = (await client.get("/api/v1/agent/review-tasks?status=open", headers=staff)).json()
    assert str(task.id) in {item["id"] for item in queue["items"]}
    detail = (await client.get(f"/api/v1/agent/review-tasks/{task.id}", headers=staff)).json()
    assert detail["snapshot"]["data"]["name"] == "Jane Doe"
    assert detail["snapshot"]["quote"]["premium"]["amountMinor"] == 7500
    assert [m["content"] for m in detail["messagesSinceOpened"] if m["role"] == "customer"] == [
        "any news?",
        "still there?",
    ]

    assert (
        await client.post(f"/api/v1/agent/review-tasks/{task.id}/claim", headers=staff)
    ).status_code == 200
    response = await client.post(
        f"/api/v1/agent/review-tasks/{task.id}/resolve",
        json={"action": "approve", "notes": "Welcome aboard."},
        headers=staff,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task"]["status"] == "resolved"
    assert body["task"]["resolution"] == "approved"
    assert "approved your application" in body["customerMessage"]["content"]
    assert "Welcome aboard." in body["customerMessage"]["content"]

    again = await client.post(
        f"/api/v1/agent/review-tasks/{task.id}/resolve", json={"action": "reject"}, headers=staff
    )
    assert again.status_code == 409

    polled = (
        await client.get(f"/api/v1/chat/sessions/{conversation_id}/messages", headers=headers)
    ).json()
    assert polled["status"] == "active"
    assert "approved your application" in polled["messages"][-1]["content"]

    async with prepared_db.session() as session:
        application = (
            await session.execute(
                select(Application).where(Application.conversation_id == uuid.UUID(conversation_id))
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent.event_type).where(
                        OutboxEvent.payload["data"]["customerId"].astext
                        == str(application.customer_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert application.status == "approved"
    assert sorted(events) == ["application.reviewed", "application.submitted", "lead.created"]


async def test_customer_can_withdraw_while_waiting(client: Any, prepared_db: Any) -> None:
    conversation_id, headers = await submit_application(client)
    _, events = await say(client, conversation_id, headers, "Please withdraw my application")
    done = completed(events)
    assert "withdrawn" in done["text"]
    assert done["conversationStatus"] == "active"
    task = await open_task(prepared_db, conversation_id)
    assert task.status == "resolved"
    assert task.resolution == "withdrawn"


async def test_changes_requested_then_resubmitted_as_new_snapshot(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    conversation_id, headers = await submit_application(client)
    task = await open_task(prepared_db, conversation_id)
    staff = await agent_headers(client, settings)
    response = await client.post(
        f"/api/v1/agent/review-tasks/{task.id}/resolve",
        json={"action": "request_changes", "notes": "Please confirm your phone number."},
        headers=staff,
    )
    assert "needs a change" in response.json()["customerMessage"]["content"]

    _, events = await say(client, conversation_id, headers, "Sure, my phone is +1 555 010 2233")
    summary = completed(events)["text"]
    assert "***2233" in summary
    events = await act(client, conversation_id, headers, "Submit", "submit")
    assert completed(events)["conversationStatus"] == "awaiting_review"
    new_task = await open_task(prepared_db, conversation_id)
    assert new_task.id != task.id
    assert new_task.snapshot_version == 2


async def test_handoff_takeover_and_return(client: Any, prepared_db: Any, settings: Any) -> None:
    conversation_id, headers = await new_session(client)
    _, events = await say(client, conversation_id, headers, "I'd like to talk to a human please")
    done = completed(events)
    assert "asked a licensed agent to join" in done["text"]
    assert done["conversationStatus"] == "human_takeover"

    staff = await agent_headers(client, settings)
    task = await open_task(prepared_db, conversation_id)
    reply = await client.post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={"text": "Hi, I'm Sam from the agency."},
        headers=staff,
    )
    assert reply.status_code == 200
    _, events = await say(client, conversation_id, headers, "Hi Sam, thanks")
    assert completed(events)["messageId"] is None  # the human answers, not the assistant

    resolved = await client.post(
        f"/api/v1/agent/review-tasks/{task.id}/resolve",
        json={"action": "return_to_assistant"},
        headers=staff,
    )
    assert "back with the PolicyPilot assistant" in resolved.json()["customerMessage"]["content"]
    _, events = await say(client, conversation_id, headers, "hello")
    assert "I can help you compare" in completed(events)["text"]


async def test_prepare_review_is_replay_safe(client: Any, prepared_db: Any, app: Any) -> None:
    from policypilot.agents.context import AgentContext
    from policypilot.agents.review_nodes import prepare_review
    from policypilot.observability.tracer import RunTrace

    conversation_id, _ = await new_session(client)
    state = app.state.policypilot
    async with prepared_db.session() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
    turn_id = uuid.uuid4()
    ctx = AgentContext(
        db=state.db,
        settings=state.settings,
        models=state.models,
        embedder=state.embedder,
        templates=state.templates,
        conversation_id=conversation.id,
        customer_id=conversation.customer_id,
        channel="web",
        locale="en",
        turn_id=turn_id,
        trace=RunTrace(conversation.id, "web", turn_id),
    )

    class Runtime:
        context = ctx

    graph_state: Any = {"messages": [], "review_request": {"summary": "wants a human"}}
    first = await prepare_review(graph_state, Runtime())  # type: ignore[arg-type]
    second = await prepare_review(graph_state, Runtime())  # type: ignore[arg-type]
    assert first["review"]["review_task_id"] == second["review"]["review_task_id"]
    async with prepared_db.session() as session:
        tasks = await session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(ReviewTask.conversation_id == conversation.id)
        )
        events = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.payload["data"]["conversationId"].astext == conversation_id)
        )
    assert tasks == 1
    assert events == 1


async def test_agent_auth_is_required(client: Any) -> None:
    assert (await client.get("/api/v1/agent/review-tasks")).status_code == 401
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": "agent@policypilot.test", "password": "nope"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"
