"""Regression tests for the fixes from review 02 (docs/reviews/02-phase1-review.md)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from tests.helpers import new_session

from policypilot.db.models import Conversation, Quote

pytestmark = pytest.mark.db


async def _context(client: Any, app: Any, prepared_db: Any) -> Any:
    from policypilot.agents.context import AgentContext
    from policypilot.observability.tracer import RunTrace

    conversation_id, _ = await new_session(client)
    state = app.state.policypilot
    async with prepared_db.session() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
    turn_id = uuid.uuid4()
    return AgentContext(
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


async def _life_product_id(prepared_db: Any) -> str:
    from policypilot.services.catalog import list_products

    async with prepared_db.session() as session:
        products = await list_products(session, line="life")
    return str(next(p.id for p in products if p.code == "KESTREL-TERM-20"))


# ---------------------------------------------------------------- finding 12


@pytest.mark.parametrize(
    ("customer_text", "expected_cover"),
    [
        ("What would $750k of cover cost?", 75_000_000),  # stated: the override is used
        ("What about a bit more cover?", 50_000_000),  # invented by the model: ignored
    ],
)
async def test_coverage_override_must_be_stated_by_the_customer(
    client: Any, app: Any, prepared_db: Any, customer_text: str, expected_cover: int
) -> None:
    from policypilot.agents.tools import AdvisorTools
    from policypilot.domain.profile import CustomerProfile

    ctx = await _context(client, app, prepared_db)
    profile = CustomerProfile(age=40, coverage_goal_minor=50_000_000, term_years=20, lines=["life"])
    tools = AdvisorTools(ctx, profile, lambda _: None, customer_text=customer_text)
    call = {
        "name": "get_quote",
        "id": "call-1",
        "args": {"product_id": await _life_product_id(prepared_db), "coverage_override": 750000},
    }
    result = json.loads(str((await tools.execute(call)).content))
    async with prepared_db.session() as session:
        quote = (
            await session.execute(select(Quote).where(Quote.id == uuid.UUID(result["quote_id"])))
        ).scalar_one()
    assert quote.inputs["coverage_minor"] == expected_cover
    assert ("coverage_override_ignored" in result) == (expected_cover == 50_000_000)


# ---------------------------------------------------------------- finding 13


async def _outbox(client: Any, user: str, count: int) -> list[Any]:
    import asyncio
    import time

    deadline, items = time.monotonic() + 30, []
    while time.monotonic() < deadline:
        items = (await client.get(f"/dev/whatsapp/outbox?userId={user}&after=0")).json()["items"]
        if len(items) >= count:
            return items
        await asyncio.sleep(0.2)
    raise AssertionError(f"expected {count} outbound messages, got {items}")


async def test_stop_suppresses_queued_and_later_messages(client: Any, prepared_db: Any) -> None:
    from datetime import UTC, datetime, timedelta

    from policypilot.db.models import ChannelIdentity, OutboundMessage

    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hello"})
    await _outbox(client, user, 2)
    channel = client._transport.app.state.policypilot.whatsapp
    async with prepared_db.transaction() as session:
        customer_id = (
            await session.execute(
                select(ChannelIdentity.customer_id).where(ChannelIdentity.external_id == user)
            )
        ).scalar_one()
        conversation_id = (
            await session.execute(
                select(Conversation.id).where(
                    Conversation.customer_id == customer_id, Conversation.channel == "whatsapp"
                )
            )
        ).scalar_one()
        queued = OutboundMessage(
            conversation_id=conversation_id,
            channel="whatsapp",
            kind="text",
            payload={"text": "queued before STOP"},
            next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(queued)
        await session.flush()
        queued_id = queued.id

    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "STOP"})
    items = await _outbox(client, user, 3)
    assert "opted out" in items[-1]["payload"]["text"]  # the confirmation itself is sent

    async with prepared_db.transaction() as session:
        row = await session.get(OutboundMessage, queued_id)
        row.next_attempt_at = datetime.now(UTC)
    await channel.send_pending()
    await channel.deliver(conversation_id, "Your application was approved.", {})
    await channel.send_pending()
    async with prepared_db.session() as session:
        row = await session.get(OutboundMessage, queued_id)
        pending = (
            await session.execute(
                select(OutboundMessage).where(
                    OutboundMessage.conversation_id == conversation_id,
                    OutboundMessage.status == "pending",
                )
            )
        ).all()
    assert (row.status, row.last_error) == ("failed", "opted_out")
    assert pending == []
    assert len((await client.get(f"/dev/whatsapp/outbox?userId={user}")).json()["items"]) == 3

    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hi again"})
    assert len(await _outbox(client, user, 4)) >= 4  # writing again lifts the opt-out


# ---------------------------------------------------------------- finding 14


async def test_a_person_is_reachable_when_the_model_budget_is_spent(
    client: Any, prepared_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.helpers import completed, say

    from policypilot.agents.context import AgentContext
    from policypilot.agents.models import BudgetCallback
    from policypilot.db.models import ReviewTask

    async def spent() -> bool:
        return False

    # The scripted model skips the budget, so every model call is made to hit an empty budget.
    monkeypatch.setattr(
        AgentContext, "llm_config", lambda self: {"callbacks": [BudgetCallback(spent)]}
    )
    conversation_id, headers = await new_session(client)
    _, events = await say(client, conversation_id, headers, "I'm looking for life insurance")
    busy = completed(events)
    assert "usage limit" in busy["text"]
    assert "asked an agent" not in busy["text"]  # no false promise

    _, events = await say(client, conversation_id, headers, "I want to talk to a real person")
    assert completed(events)["conversationStatus"] == "human_takeover"
    async with prepared_db.session() as session:
        task = (
            await session.execute(
                select(ReviewTask).where(ReviewTask.conversation_id == uuid.UUID(conversation_id))
            )
        ).scalar_one()
    assert task.kind == "handoff"
    assert task.summary.startswith("Customer asked to talk to a person")


# ---------------------------------------------------------------- finding 3


async def test_review_decisions_keep_contact_details_out_of_the_graph(
    client: Any, prepared_db: Any, settings: Any, app: Any
) -> None:
    from tests.integration.test_review_flow import agent_headers, open_task, submit_application

    from policypilot.db.models import ApplicationSnapshot, ReviewTask

    conversation_id, _ = await submit_application(client)
    task = await open_task(prepared_db, conversation_id)
    response = await client.post(
        f"/api/v1/agent/review-tasks/{task.id}/resolve",
        json={
            "action": "edit_and_approve",
            "notes": "Approved. Questions? Write to jane.new@example.test or call +1 415 555 0100.",
            "clericalEdits": {"email": "jane.new@example.test"},
        },
        headers=await agent_headers(client, settings),
    )
    assert response.status_code == 200, response.text
    reply = response.json()["customerMessage"]["content"]
    assert "jane.new@example.test" not in reply  # the note is masked
    assert "555 0100" not in reply

    thread = await app.state.policypilot.graph.aget_state(
        {"configurable": {"thread_id": conversation_id}}
    )
    checkpointed = json.dumps(thread.values, default=str)
    for secret in ("jane.new@example.test", "555 0100", "agent@policypilot.test"):
        assert secret not in checkpointed, secret

    async with prepared_db.session() as session:  # the clerical edit still reached the snapshot
        record = await session.get(ReviewTask, task.id)
        snapshot = (
            await session.execute(
                select(ApplicationSnapshot)
                .where(ApplicationSnapshot.application_id == record.application_id)
                .order_by(ApplicationSnapshot.version.desc())
                .limit(1)
            )
        ).scalar_one()
    assert snapshot.data["email"] == "jane.new@example.test"


# ---------------------------------------------------------------- finding 11


async def _quote_ages(prepared_db: Any, quote_ids: list[str]) -> set[int]:
    async with prepared_db.session() as session:
        rows = await session.execute(
            select(Quote.inputs).where(Quote.id.in_([uuid.UUID(q) for q in quote_ids]))
        )
        return {inputs["age"] for inputs in rows.scalars()}


async def test_a_correction_is_applied_before_quoting_again(client: Any, prepared_db: Any) -> None:
    from tests.helpers import artifacts, say

    conversation_id, headers = await new_session(client)
    _, events = await say(client, conversation_id, headers, "I'm 40, $500k of life cover, 20 years")
    first = [a["quoteId"] for a in artifacts(events, "quote")]
    _, events = await say(client, conversation_id, headers, "Actually I'm 50, quote again please")
    second = [a["quoteId"] for a in artifacts(events, "quote")]
    assert await _quote_ages(prepared_db, first) == {40}
    assert await _quote_ages(prepared_db, second) == {50}


async def test_the_application_is_requoted_when_the_inputs_change(
    client: Any, prepared_db: Any
) -> None:
    from tests.helpers import artifacts, completed, say
    from tests.integration.test_review_flow import act

    from policypilot.db.models import Application

    conversation_id, headers = await new_session(client)
    await say(client, conversation_id, headers, "I'm 40 and I'd like life insurance")
    _, events = await say(client, conversation_id, headers, "I want $500k of cover for 20 years")
    kestrel = next(
        c for c in artifacts(events, "quote") if c["productName"] == "Kestrel Term Life 20"
    )
    await say(client, conversation_id, headers, "I'd like to apply please")
    await act(client, conversation_id, headers, "Kestrel", f"choose:{kestrel['quoteId']}")
    await say(client, conversation_id, headers, "My name is Jane Doe, email jane@example.test")
    summary = completed(await act(client, conversation_id, headers, "Yes", "consent_yes"))
    assert "$75.00 per month" in summary["text"]

    _, events = await say(client, conversation_id, headers, "Actually I'm 50")
    corrected = completed(events)["text"]
    assert "$75.00 per month" not in corrected  # a new summary with the new price, not submitted
    assert "age 50" in corrected
    assert completed(events)["conversationStatus"] == "active"

    done = completed(await act(client, conversation_id, headers, "Submit", "submit"))
    assert done["conversationStatus"] == "awaiting_review"
    async with prepared_db.session() as session:
        application = (
            await session.execute(
                select(Application).where(Application.conversation_id == uuid.UUID(conversation_id))
            )
        ).scalar_one()
    assert await _quote_ages(prepared_db, [str(application.quote_id)]) == {50}


# ---------------------------------------------------------------- findings 5 and 8


async def test_a_turn_abandoned_by_a_crash_can_be_retried(client: Any, prepared_db: Any) -> None:
    from tests.helpers import completed

    from policypilot.db.models import Turn

    conversation_id, headers = await new_session(client)
    async with prepared_db.transaction() as session:  # what a crash mid-turn leaves behind
        session.add(
            Turn(
                conversation_id=uuid.UUID(conversation_id),
                client_message_id="c-crashed-0001",
                status="running",
            )
        )
    response = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={"text": "hello", "clientMessageId": "c-crashed-0001"},
        headers=headers,
    )
    assert response.status_code == 200  # was 409 turn_in_progress forever
    from tests.helpers import parse_sse

    assert completed(parse_sse(response.text))["conversationStatus"] == "active"


async def test_a_full_runner_answers_503_before_taking_the_lock(client: Any) -> None:
    import asyncio

    runner = client._transport.app.state.policypilot.runner
    conversation_id, headers = await new_session(client)
    saved = runner.semaphore
    runner.semaphore = asyncio.Semaphore(0)
    try:
        response = await client.post(
            f"/api/v1/chat/sessions/{conversation_id}/messages",
            json={"text": "hello", "clientMessageId": "c-overload-0001"},
            headers=headers,
        )
    finally:
        runner.semaphore = saved
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    from policypilot.services.locks import conversation_lock

    async with conversation_lock(runner.db, uuid.UUID(conversation_id), wait_seconds=1):
        pass  # the lock was never taken


# ---------------------------------------------------------------- findings 6, 7, 17 and 20


async def _whatsapp_conversation(prepared_db: Any, user: str) -> uuid.UUID:
    from policypilot.db.models import ChannelIdentity

    async with prepared_db.session() as session:
        customer_id = (
            await session.execute(
                select(ChannelIdentity.customer_id).where(ChannelIdentity.external_id == user)
            )
        ).scalar_one()
        return (
            await session.execute(
                select(Conversation.id).where(
                    Conversation.customer_id == customer_id, Conversation.channel == "whatsapp"
                )
            )
        ).scalar_one()


async def test_whatsapp_keeps_the_redaction_notice(client: Any) -> None:
    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post(
        "/dev/whatsapp/simulate",
        json={"userId": user, "text": "My card is 4111 1111 1111 1111, I want car insurance"},
    )
    items = await _outbox(client, user, 2)
    assert "removed what looked like a card" in items[-1]["payload"]["text"]


async def test_a_recovered_whatsapp_event_is_not_run_twice(client: Any, prepared_db: Any) -> None:
    from policypilot.db.models import InboundEvent, Message

    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hello"})
    await _outbox(client, user, 2)
    conversation_id = await _whatsapp_conversation(prepared_db, user)
    async with prepared_db.session() as session:
        event = (
            await session.execute(
                select(InboundEvent).where(InboundEvent.conversation_id == conversation_id)
            )
        ).scalar_one()
        before = len(
            (
                await session.execute(
                    select(Message.id).where(Message.conversation_id == conversation_id)
                )
            ).all()
        )
    channel = client._transport.app.state.policypilot.whatsapp
    # As if the turn finished but a crash kept the event from being marked done.
    await channel._process(conversation_id, event.id, event.kind, dict(event.payload))
    async with prepared_db.session() as session:
        after = len(
            (
                await session.execute(
                    select(Message.id).where(Message.conversation_id == conversation_id)
                )
            ).all()
        )
    assert after == before


async def test_a_retry_is_sent_without_new_activity(client: Any, prepared_db: Any) -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta

    from policypilot.db.models import OutboundMessage

    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hello"})
    await _outbox(client, user, 2)
    conversation_id = await _whatsapp_conversation(prepared_db, user)
    async with prepared_db.transaction() as session:
        row = OutboundMessage(
            conversation_id=conversation_id,
            channel="whatsapp",
            kind="text",
            payload={"text": "retried later"},
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        session.add(row)
        await session.flush()
        row_id = row.id
    channel = client._transport.app.state.policypilot.whatsapp
    await channel.send_pending()  # nothing due yet: schedules a wake-up instead of giving up
    await asyncio.sleep(3)
    async with prepared_db.session() as session:
        assert (await session.get(OutboundMessage, row_id)).status == "sent"


async def test_maintenance_runners_can_deliver_to_whatsapp(prepared_db: Any, settings: Any) -> None:
    from policypilot.services.maintenance import _with_runner

    async def job(runner: Any) -> bool:
        return runner.deliver is not None

    assert await _with_runner(prepared_db, settings, job)


# ---------------------------------------------------------------- finding 9 (contained part)


async def test_agent_replies_and_closure_respect_the_conversation_lock(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update
    from tests.helpers import say
    from tests.integration.test_review_flow import agent_headers

    from policypilot.services.locks import conversation_lock
    from policypilot.services.maintenance import close_inactive

    conversation_id, headers = await new_session(client)
    await say(client, conversation_id, headers, "I want to talk to a real person")
    agent = await agent_headers(client, settings)
    url = f"/api/v1/agent/conversations/{conversation_id}/messages"
    db = client._transport.app.state.policypilot.db
    async with conversation_lock(db, uuid.UUID(conversation_id)):  # a customer turn is running
        busy = await client.post(url, json={"text": "Hello, I'm Sam."}, headers=agent)
    assert busy.status_code == 409
    assert busy.json()["code"] == "conversation_busy"
    assert (
        await client.post(url, json={"text": "Hello, I'm Sam."}, headers=agent)
    ).status_code == 200

    async with prepared_db.transaction() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == uuid.UUID(conversation_id))
            .values(status="active", last_activity_at=datetime.now(UTC) - timedelta(days=60))
        )
    async with conversation_lock(db, uuid.UUID(conversation_id)):
        await close_inactive(prepared_db, settings)
        async with prepared_db.session() as session:
            assert (await session.get(Conversation, uuid.UUID(conversation_id))).status == "active"
    await close_inactive(prepared_db, settings)
    async with prepared_db.session() as session:
        assert (await session.get(Conversation, uuid.UUID(conversation_id))).status == "closed"


# ---------------------------------------------------------------- finding 21


async def test_reindexing_keeps_citation_ids(prepared_db: Any, settings: Any) -> None:
    from policypilot.db.models import DocumentChunk, PolicyDocument
    from policypilot.rag.embeddings import HashingEmbedder
    from policypilot.seed.run import run_seed

    async def chunk_ids() -> list[int]:
        async with prepared_db.session() as session:
            return list(
                (
                    await session.execute(
                        select(DocumentChunk.id)
                        .join(PolicyDocument, PolicyDocument.id == DocumentChunk.document_id)
                        .where(PolicyDocument.is_current.is_(True))
                        .order_by(DocumentChunk.id)
                    )
                ).scalars()
            )

    before = await chunk_ids()
    report = await run_seed(
        prepared_db, settings, HashingEmbedder(settings.embedding_dimensions), reindex=True
    )
    assert report.documents_ingested  # every document was re-embedded
    assert await chunk_ids() == before  # and every citation id survived


# ---------------------------------------------------------------- findings 25 and 27


async def test_long_transcripts_are_paged_to_the_end(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    from tests.integration.test_review_flow import agent_headers

    from policypilot.db.models import Message

    conversation_id, headers = await new_session(client)
    async with prepared_db.transaction() as session:
        for i in range(205):
            session.add(
                Message(
                    conversation_id=uuid.UUID(conversation_id), role="customer", content=f"m{i}"
                )
            )
    url = f"/api/v1/chat/sessions/{conversation_id}/messages"
    first = (await client.get(url, headers=headers)).json()
    assert len(first["messages"]) == 200
    assert first["hasMore"] is True
    rest = (await client.get(url, params={"after": first["nextAfter"]}, headers=headers)).json()
    assert rest["hasMore"] is False
    assert rest["messages"][-1]["content"] == "m204"  # the latest message is reachable

    agent = await agent_headers(client, settings)
    detail = (
        await client.get(f"/api/v1/agent/conversations/{conversation_id}", headers=agent)
    ).json()
    assert detail["messages"][-1]["content"] == "m204"


async def test_mcp_leads_show_contact_details_in_the_desk(
    client: Any, app: Any, settings: Any
) -> None:
    from tests.integration.test_mcp import mcp_session
    from tests.integration.test_review_flow import agent_headers

    email = f"lee.{uuid.uuid4().hex[:6]}@example.test"
    async with mcp_session(app, settings.mcp_dev_token.get_secret_value()) as session:
        result = await session.call_tool(
            "create_lead",
            {
                "lead": {
                    "name": "Lee",
                    "email": email,
                    "line_interest": ["auto"],
                    "consent_contact": True,
                    "consent_text_version": "test-v1",
                }
            },
        )
        assert not result.is_error
    leads = (
        await client.get("/api/v1/agent/leads", headers=await agent_headers(client, settings))
    ).json()["items"]
    lead = next(item for item in leads if item["contact"]["email"] == email)
    assert lead["consent"]["at"] is not None
    assert lead["consent"]["textVersion"]  # the version of the consent text on record
