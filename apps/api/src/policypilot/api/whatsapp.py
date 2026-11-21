"""WhatsApp webhook and the local simulator routes (docs/design/whatsapp.md, ADR-0009)."""

from __future__ import annotations

import hmac
import json
import time
import uuid
from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select, update

from policypilot.api.state import AppState, app_state
from policypilot.channels.whatsapp.payloads import parse, sign, simulated_payload, verify_signature
from policypilot.db.models import ChannelIdentity, Conversation, SimulatorOutbox

log = structlog.get_logger(__name__)
MAX_BODY = 1_000_000

webhook_router = APIRouter(tags=["whatsapp"])
dev_router = APIRouter(prefix="/dev/whatsapp", tags=["dev"])


@webhook_router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def verify(
    request: Request,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    expected = app_state(request).settings.whatsapp_verify_token.get_secret_value()
    if mode == "subscribe" and token and expected and hmac.compare_digest(token, expected):
        return challenge or ""
    raise HTTPException(403, detail={"code": "verification_failed", "message": "Forbidden."})


async def handle_webhook(state: AppState, raw: bytes, signature: str | None) -> Response:
    if len(raw) > MAX_BODY:
        raise HTTPException(413, detail={"code": "payload_too_large", "message": "Too large."})
    if not verify_signature(state.settings.whatsapp_app_secret.get_secret_value(), raw, signature):
        log.warning("whatsapp.invalid_signature")  # never logs the body
        raise HTTPException(401, detail={"code": "invalid_signature", "message": "Bad signature."})
    try:
        messages, statuses = parse(json.loads(raw))
    except ValueError as exc:
        raise HTTPException(
            400, detail={"code": "invalid_payload", "message": "Bad JSON."}
        ) from exc
    if state.whatsapp is None:
        raise HTTPException(503, detail={"code": "channel_unavailable", "message": "Retry."})
    try:
        await state.whatsapp.accept(messages, statuses)
    except Exception as exc:
        # Not durably stored: answer 503 so Meta retries (for up to 7 days).
        log.exception("whatsapp.accept_failed")
        raise HTTPException(503, detail={"code": "not_accepted", "message": "Retry."}) from exc
    return Response(status_code=200)


@webhook_router.post("/webhooks/whatsapp")
async def receive(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> Response:
    return await handle_webhook(app_state(request), await request.body(), x_hub_signature_256)


# --------------------------------------------------------------------------- local simulator


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SimulateRequest(_Camel):
    user_id: str = Field(pattern=r"^sim-[A-Za-z0-9-]{4,40}$")
    name: str = Field(default="Simulator User", max_length=60)
    text: str | None = Field(default=None, max_length=2000)
    button_id: str | None = Field(default=None, max_length=256)


class AdvanceClock(_Camel):
    user_id: str = Field(pattern=r"^sim-[A-Za-z0-9-]{4,40}$")
    hours: int = Field(ge=1, le=72)


@dev_router.post("/simulate")
async def simulate(body: SimulateRequest, request: Request) -> dict[str, Any]:
    """Builds and signs a Meta-shaped payload, then runs it through the real webhook handler."""
    state = app_state(request)
    if not body.text and not body.button_id:
        raise HTTPException(422, detail={"code": "empty", "message": "text or buttonId required."})
    payload = simulated_payload(
        user_id=body.user_id,
        name=body.name,
        message_id=f"wamid.sim-{uuid.uuid4()}",
        text=body.text,
        button_id=body.button_id,
        timestamp=int(time.time()),
    )
    raw = json.dumps(payload).encode()
    signature = sign(state.settings.whatsapp_app_secret.get_secret_value(), raw)
    await handle_webhook(state, raw, signature)
    return {"accepted": True}


@dev_router.get("/outbox")
async def outbox(
    request: Request,
    user_id: Annotated[str, Query(alias="userId", pattern=r"^sim-[A-Za-z0-9-]{4,40}$")],
    after: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    state = app_state(request)
    async with state.db.session() as session:
        rows = (
            (
                await session.execute(
                    select(SimulatorOutbox)
                    .where(SimulatorOutbox.to_external_id == user_id, SimulatorOutbox.id > after)
                    .order_by(SimulatorOutbox.id)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {"id": r.id, "payload": r.payload, "createdAt": r.created_at.isoformat()} for r in rows
        ],
        "nextAfter": rows[-1].id if rows else after,
    }


@dev_router.post("/advance-clock")
async def advance_clock(body: AdvanceClock, request: Request) -> dict[str, bool]:
    """Moves the 24-hour window back to demonstrate the template fallback."""
    state = app_state(request)
    async with state.db.transaction() as session:
        customer_ids = select(ChannelIdentity.customer_id).where(
            ChannelIdentity.channel == "whatsapp", ChannelIdentity.external_id == body.user_id
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.customer_id.in_(customer_ids), Conversation.channel == "whatsapp")
            .values(
                last_customer_message_at=Conversation.last_customer_message_at
                - timedelta(hours=body.hours)
            )
        )
    return {"ok": True}
