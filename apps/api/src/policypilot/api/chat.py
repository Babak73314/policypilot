"""Customer chat: sessions, turns over SSE, and transcript polling (docs/design/api.md)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.sse import KEEPALIVE_COMMENT, EventSourceResponse, format_sse_event
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from policypilot.agents.runner import (
    ConversationNotFoundError,
    RunnerOverloadedError,
    TurnInProgressError,
)
from policypilot.api.state import AppState, app_state, client_ip
from policypilot.security.auth import InvalidTokenError, issue_customer_token, verify_customer_token
from policypilot.security.ingress import sanitize
from policypilot.services import conversations

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
log = structlog.get_logger(__name__)


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SessionCreate(_Camel):
    locale: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")


class TurnRequest(_Camel):
    text: str = Field(min_length=1)
    client_message_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    action: str | None = Field(default=None, max_length=64)


def _token(state: AppState, conversation_id: uuid.UUID, version: int) -> str:
    return issue_customer_token(
        state.settings.app_secret_key.get_secret_value(),
        conversation_id,
        version,
        state.settings.customer_token_ttl_hours,
    )


async def customer_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> tuple[AppState, Any]:
    state = app_state(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            401, detail={"code": "missing_token", "message": "Bearer token required."}
        )
    try:
        claims = verify_customer_token(
            state.settings.app_secret_key.get_secret_value(), authorization[7:].strip()
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            401, detail={"code": "invalid_token", "message": "Invalid token."}
        ) from exc
    if claims.conversation_id != conversation_id:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Not your conversation."})
    async with state.db.session() as session:
        conversation = await conversations.get_conversation(session, conversation_id)
    if conversation is None or conversation.token_version != claims.version:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Token revoked."})
    return state, conversation


Customer = Annotated[tuple[AppState, Any], Depends(customer_conversation)]


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: SessionCreate | None = None) -> dict[str, Any]:
    state = app_state(request)
    state.limiter.check(f"session:{client_ip(request)}", 10, 3600)
    locale = (body or SessionCreate()).locale
    disclosure = state.templates.text(
        "ai_disclosure", "web", locale, agency_name=state.settings.agency_name
    )
    async with state.db.transaction() as session:
        conversation = await conversations.create_web_conversation(
            session, locale=locale, disclosure=disclosure
        )
    return {
        "conversationId": str(conversation.id),
        "token": _token(state, conversation.id, conversation.token_version or 1),
        "locale": locale,
        "disclosure": disclosure,
        "privacyUrl": "/privacy",
        "demoMode": state.settings.demo_mode,
    }


@router.get("/sessions/{conversation_id}/messages")
async def get_messages(
    conversation_id: uuid.UUID,
    customer: Customer,
    response: Response,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> dict[str, Any]:
    state, conversation = customer
    async with state.db.session() as session:
        messages = await conversations.list_messages(session, conversation_id, after=after)
    response.headers["X-Conversation-Token"] = _token(
        state, conversation_id, conversation.token_version
    )
    views = [conversations.message_view(m) for m in messages if m.content or m.role != "assistant"]
    return {
        "status": conversation.status,
        "messages": views,
        "nextAfter": messages[-1].id if messages else after,
        # A full page means there may be more: clients read on from nextAfter (review 02, #25).
        "hasMore": len(messages) == conversations.MESSAGE_PAGE,
    }


@router.post("/sessions/{conversation_id}/messages", response_model=None)
async def post_message(
    conversation_id: uuid.UUID, body: TurnRequest, request: Request, customer: Customer
) -> EventSourceResponse:
    state, conversation = customer
    if len(body.text) > state.settings.chat_max_message_chars:
        raise HTTPException(
            413, detail={"code": "message_too_long", "message": "Message too long."}
        )
    state.limiter.check(f"turn:{conversation_id}", 20, 60)
    state.limiter.check(f"turn-ip:{client_ip(request)}", 60, 60)
    token = _token(state, conversation_id, conversation.token_version)
    headers = {"X-Conversation-Token": token, "Cache-Control": "no-store"}

    try:
        replay = await state.runner.replay(conversation_id, body.client_message_id)
    except TurnInProgressError as exc:
        raise HTTPException(409, detail={"code": "turn_in_progress", "message": "Busy."}) from exc
    if replay is not None:
        return EventSourceResponse(_replay_stream(replay, token), headers=headers)

    text = sanitize(body.text, source="customer")
    try:
        prepared = await state.runner.begin(
            conversation_id, text, body.client_message_id, action=body.action
        )
    except TurnInProgressError as exc:
        raise HTTPException(409, detail={"code": "turn_in_progress", "message": "Busy."}) from exc
    except RunnerOverloadedError as exc:
        raise HTTPException(
            503,
            detail={"code": "overloaded", "message": "Too many conversations; retry shortly."},
            headers={"Retry-After": "5"},
        ) from exc
    except ConversationNotFoundError as exc:
        raise HTTPException(
            404, detail={"code": "conversation_closed", "message": "Closed."}
        ) from exc

    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        if event == "message.completed":
            data = {**data, "token": token}
        await queue.put((event, data))

    async def run() -> None:
        try:
            await state.runner.execute(prepared, emit)
        except Exception as exc:  # reported as an `error` event; the runner logged the trace
            log.warning("turn.task_failed", error=type(exc).__name__)
        finally:
            await queue.put(None)

    # The turn runs independently of the HTTP connection: a client disconnect does not cancel it.
    task = asyncio.create_task(run())
    state.background.add(task)
    task.add_done_callback(state.background.discard)
    return EventSourceResponse(_queue_stream(queue), headers=headers)


KEEPALIVE_SECONDS = 15.0


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return format_sse_event(event=event, data_str=json.dumps(data, default=str))


async def _queue_stream(
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None],
) -> AsyncIterator[bytes]:
    """Relay runner events; send a keep-alive comment every 15 s so proxies keep the stream."""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
        except TimeoutError:
            yield KEEPALIVE_COMMENT
            continue
        if item is None:
            return
        event, data = item
        yield _sse(event, data)


async def _replay_stream(replay: Any, token: str) -> AsyncIterator[bytes]:
    message = replay.message or {}
    yield _sse("message.delta", {"text": message.get("content", "")})
    yield _sse(
        "message.completed",
        {
            "messageId": message.get("id"),
            "text": message.get("content", ""),
            "citations": message.get("citations", []),
            "quoteIds": [
                a.get("quoteId") for a in message.get("artifacts", []) if a.get("type") == "quote"
            ],
            "actions": message.get("actions", []),
            "conversationStatus": replay.conversation_status,
            "token": token,
            "replayed": True,
        },
    )
