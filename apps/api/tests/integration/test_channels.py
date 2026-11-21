"""WhatsApp webhook and simulator, outbox dispatch to n8n, and the n8n integration API."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select

from policypilot.channels.whatsapp.payloads import sign, simulated_payload
from policypilot.db.models import InboundEvent, Lead, OutboundMessage, OutboxEvent

pytestmark = pytest.mark.db


async def wait_for_outbox(client: Any, user_id: str, count: int, after: int = 0) -> list[Any]:
    deadline = time.monotonic() + 30
    items: list[Any] = []
    while time.monotonic() < deadline:
        body = (await client.get(f"/dev/whatsapp/outbox?userId={user_id}&after={after}")).json()
        items = body["items"]
        if len(items) >= count:
            return items
        await asyncio.sleep(0.2)
    raise AssertionError(f"expected {count} outbound messages, got {items}")


async def test_verification_handshake(client: Any, settings: Any) -> None:
    token = settings.whatsapp_verify_token.get_secret_value()
    ok = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"},
    )
    assert ok.status_code == 200
    assert ok.text == "42"
    bad = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )
    assert bad.status_code == 403


async def test_unsigned_or_forged_payloads_are_rejected(client: Any) -> None:
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    assert (await client.post("/webhooks/whatsapp", content=body)).status_code == 401
    forged = await client.post(
        "/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}
    )
    assert forged.status_code == 401


async def test_simulated_conversation_with_disclosure_and_reply(client: Any) -> None:
    user = f"sim-{uuid.uuid4().hex[:12]}"
    response = await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hello"})
    assert response.json() == {"accepted": True}
    items = await wait_for_outbox(client, user, 2)
    assert "AI assistant" in items[0]["payload"]["text"]  # disclosure first
    assert "compare insurance" in items[1]["payload"]["text"]


async def test_duplicate_deliveries_are_accepted_once(
    client: Any, settings: Any, prepared_db: Any
) -> None:
    user = f"sim-{uuid.uuid4().hex[:12]}"
    payload = simulated_payload(
        user_id=user,
        name="Dup",
        message_id=f"wamid.{uuid.uuid4()}",
        text="hello",
        button_id=None,
        timestamp=int(time.time()),
    )
    raw = json.dumps(payload).encode()
    headers = {"X-Hub-Signature-256": sign(settings.whatsapp_app_secret.get_secret_value(), raw)}
    for _ in range(3):  # Meta retries the same delivery
        assert (
            await client.post("/webhooks/whatsapp", content=raw, headers=headers)
        ).status_code == 200
    await wait_for_outbox(client, user, 2)
    message_id = payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    async with prepared_db.session() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(InboundEvent)
            .where(InboundEvent.provider_message_id == message_id)
        )
    assert count == 1


async def test_outside_the_window_a_template_is_used(client: Any, prepared_db: Any) -> None:
    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "hello"})
    await wait_for_outbox(client, user, 2)
    await client.post("/dev/whatsapp/advance-clock", json={"userId": user, "hours": 25})
    channel = client._transport.app.state.policypilot.whatsapp
    async with prepared_db.session() as session:
        conversation_id = (
            await session.execute(
                select(OutboundMessage.conversation_id)
                .where(OutboundMessage.payload["text"].astext.contains("AI assistant"))
                .order_by(OutboundMessage.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    await channel.deliver(conversation_id, "Your application was approved.", {})
    items = await wait_for_outbox(client, user, 3)
    assert items[-1]["payload"]["type"] == "template"
    assert items[-1]["payload"]["name"] == "application_update_v1"


async def test_stop_opts_out(client: Any) -> None:
    user = f"sim-{uuid.uuid4().hex[:12]}"
    await client.post("/dev/whatsapp/simulate", json={"userId": user, "text": "STOP"})
    items = await wait_for_outbox(client, user, 2)
    assert "opted out" in items[-1]["payload"]["text"]


async def test_outbox_events_reach_n8n_with_auth_headers(
    app: Any, prepared_db: Any, settings: Any
) -> None:
    from policypilot.events.dispatcher import OutboxDispatcher
    from policypilot.services.events import enqueue_event

    settings_with_n8n = settings.model_copy(update={"n8n_events_url": "http://n8n.test/webhook/pp"})
    async with prepared_db.transaction() as session:
        event_id = await enqueue_event(
            session, "lead.created", {"leadId": "x"}, key=f"test:{uuid.uuid4()}"
        )
    dispatcher = OutboxDispatcher(prepared_db, settings_with_n8n)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://n8n.test/webhook/pp").mock(return_value=httpx.Response(200))
        await dispatcher.dispatch_due()
    request = route.calls.last.request
    assert request.headers["X-PolicyPilot-Event-Id"] == str(event_id)
    assert request.headers["X-PolicyPilot-Event-Type"] == "lead.created"
    assert request.headers["X-PolicyPilot-Token"] == settings.n8n_webhook_token.get_secret_value()
    async with prepared_db.session() as session:
        event = await session.get(OutboxEvent, event_id)
    assert event.delivered_at is not None
    await dispatcher.close()


async def test_failed_deliveries_back_off(prepared_db: Any, settings: Any) -> None:
    from policypilot.events.dispatcher import OutboxDispatcher
    from policypilot.services.events import enqueue_event

    settings_with_n8n = settings.model_copy(update={"n8n_events_url": "http://n8n.test/webhook/pp"})
    async with prepared_db.transaction() as session:
        event_id = await enqueue_event(
            session, "lead.created", {"leadId": "y"}, key=f"test:{uuid.uuid4()}"
        )
    dispatcher = OutboxDispatcher(prepared_db, settings_with_n8n)
    with respx.mock() as mock:
        mock.post("http://n8n.test/webhook/pp").mock(return_value=httpx.Response(503))
        remaining = await dispatcher.dispatch_due()
    async with prepared_db.session() as session:
        event = await session.get(OutboxEvent, event_id)
    assert remaining >= 1
    assert event.delivered_at is None
    assert event.attempts == 1
    assert event.available_at > event.created_at
    await dispatcher.close()


async def test_follow_up_callbacks_are_idempotent(
    client: Any, prepared_db: Any, settings: Any
) -> None:
    from policypilot.db.models import Customer

    async with prepared_db.transaction() as session:
        customer = Customer(display_name="Lee")
        session.add(customer)
        await session.flush()
        lead = Lead(customer_id=customer.id, kind="contact_request", source="web")
        session.add(lead)
        await session.flush()
        lead_id = lead.id
    headers = {"Authorization": f"Bearer {settings.n8n_integration_token.get_secret_value()}"}
    body = {"occurrenceId": f"exec-1:{lead_id}", "channel": "email"}
    first = await client.post(
        f"/api/v1/integrations/leads/{lead_id}/follow-ups", json=body, headers=headers
    )
    second = await client.post(
        f"/api/v1/integrations/leads/{lead_id}/follow-ups", json=body, headers=headers
    )
    assert first.json()["duplicate"] is False
    assert second.json() == {"duplicate": True}
    assert (await client.get("/api/v1/integrations/digest", headers=headers)).status_code == 200
    mcp_only = {"Authorization": f"Bearer {settings.mcp_dev_token.get_secret_value()}"}
    assert (await client.get("/api/v1/integrations/digest", headers=mcp_only)).status_code == 403
