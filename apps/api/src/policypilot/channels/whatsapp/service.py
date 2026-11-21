"""WhatsApp: durable acceptance, ordered per-conversation drain, outbound delivery, 24-hour window.

See docs/design/whatsapp.md. The webhook commits sanitised events before answering 200; a failed
insert answers 503 so Meta retries. Processing and sending are event-driven background tasks
(no idle polling, so a shared Aurora cluster can pause).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.agents.runner import RunnerOverloadedError, TurnInProgressError, TurnRunner
from policypilot.channels.whatsapp.payloads import InboundMessage, StatusUpdate
from policypilot.config import Settings
from policypilot.db.models import (
    ChannelIdentity,
    Conversation,
    Customer,
    InboundEvent,
    Message,
    OutboundMessage,
    SimulatorOutbox,
)
from policypilot.db.session import Database
from policypilot.security.ingress import sanitize
from policypilot.services.templates import TemplateStore

log = structlog.get_logger(__name__)
WINDOW = timedelta(hours=24) - timedelta(minutes=5)
MAX_TEXT = 4000
MAX_ATTEMPTS_IN = 3
MAX_ATTEMPTS_OUT = 5
STOP_WORDS = frozenset({"stop", "unsubscribe", "stop messaging me"})


def window_open(conversation: Conversation, now: datetime | None = None) -> bool:
    last = conversation.last_customer_message_at
    return last is not None and (now or datetime.now(UTC)) - last < WINDOW


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = paragraph[:limit]
    if current:
        parts.append(current)
    return parts[:3]


def render_for_whatsapp(text: str, metadata: dict[str, Any]) -> str:
    """Channel rendering: comparison lists instead of tables, and citation lines."""
    lines = [text.strip()]
    for artifact in metadata.get("artifacts") or []:
        if artifact.get("type") == "comparison":
            for index, column in enumerate(artifact.get("columns", [])):
                attrs = [
                    f"{row['label']}: {row['values'][index]}"
                    for row in artifact.get("rows", [])[:4]
                    if row["values"][index] is not None
                ]
                lines.append(f"{index + 1}. *{column['productName']}* - " + "; ".join(attrs))
    for citation in metadata.get("citations") or []:
        lines.append(
            f"Source: {citation['documentTitle']}, {citation['section']}, p.{citation['pages']}"
        )
    return "\n\n".join(line for line in lines if line)


class WhatsAppChannel:
    def __init__(
        self, *, db: Database, settings: Settings, runner: TurnRunner, templates: TemplateStore
    ) -> None:
        self.db = db
        self.settings = settings
        self.runner = runner
        self.templates = templates
        self._draining: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._sending: asyncio.Task[None] | None = None
        self._wakeup: asyncio.TimerHandle | None = None
        self._send_requested = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self.http = httpx.AsyncClient(timeout=15)

    # ------------------------------------------------------------------ acceptance

    async def _conversation_for(
        self, session: AsyncSession, message: InboundMessage
    ) -> tuple[uuid.UUID, bool]:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.channel == "whatsapp",
                    ChannelIdentity.external_id == message.user_key,
                )
            )
        ).scalar_one_or_none()
        if identity is None:
            customer = Customer(display_name=message.profile_name, locale="en")
            session.add(customer)
            await session.flush()
            session.add(
                ChannelIdentity(
                    customer_id=customer.id, channel="whatsapp", external_id=message.user_key
                )
            )
            customer_id = customer.id
        else:
            customer_id = identity.customer_id
        conversation = (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.customer_id == customer_id,
                    Conversation.channel == "whatsapp",
                    Conversation.status != "closed",
                )
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if conversation is not None:
            return conversation.id, False
        conversation = Conversation(
            customer_id=customer_id,
            channel="whatsapp",
            locale="en",
            ai_disclosed_at=datetime.now(UTC),
        )
        session.add(conversation)
        await session.flush()
        return conversation.id, True

    async def accept(self, messages: list[InboundMessage], statuses: list[StatusUpdate]) -> None:
        """Store events durably (deduplicated by provider id). Raises on database failure."""
        new_conversations: list[uuid.UUID] = []
        touched: set[uuid.UUID] = set()
        async with self.db.transaction() as session:
            for message in messages:
                exists = (
                    await session.execute(
                        select(InboundEvent.id).where(
                            InboundEvent.channel == "whatsapp",
                            InboundEvent.provider_message_id == message.provider_id,
                        )
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    continue
                conversation_id, created = await self._conversation_for(session, message)
                if created:
                    new_conversations.append(conversation_id)
                clean = sanitize(message.text, source="webhook") if message.text else None
                await session.execute(
                    insert(InboundEvent)
                    .values(
                        channel="whatsapp",
                        provider_message_id=message.provider_id,
                        conversation_id=conversation_id,
                        kind=message.kind,
                        payload={
                            "text": clean.stored_text if clean else "",
                            "redactions": clean.redactions if clean else {},
                            "button_id": message.button_id,
                            "timestamp": message.timestamp,
                        },
                    )
                    .on_conflict_do_nothing(index_elements=["channel", "provider_message_id"])
                )
                touched.add(conversation_id)
            for status in statuses:
                await session.execute(
                    insert(InboundEvent)
                    .values(
                        channel="whatsapp",
                        provider_message_id=f"{status.provider_id}:{status.status}",
                        kind="status",
                        payload={
                            "wamid": status.provider_id,
                            "status": status.status,
                            "error_code": status.error_code,
                        },
                        status="done",
                    )
                    .on_conflict_do_nothing(index_elements=["channel", "provider_message_id"])
                )
                await session.execute(
                    update(OutboundMessage)
                    .where(OutboundMessage.provider_message_id == status.provider_id)
                    .values(
                        status=status.status
                        if status.status in ("sent", "delivered", "read", "failed")
                        else OutboundMessage.status,
                        last_error=str(status.error_code) if status.error_code else None,
                    )
                )
            for conversation_id in new_conversations:
                disclosure = self.templates.text(
                    "ai_disclosure", "whatsapp", "en", agency_name=self.settings.agency_name
                )
                message_row = Message(
                    conversation_id=conversation_id, role="assistant", content=disclosure
                )
                session.add(message_row)
                await session.flush()
                session.add(
                    OutboundMessage(
                        conversation_id=conversation_id,
                        channel="whatsapp",
                        message_id=message_row.id,
                        kind="text",
                        payload={"text": disclosure},
                    )
                )
        for conversation_id in touched:
            self.nudge(conversation_id)
        if new_conversations:
            self.nudge_sender()

    # ------------------------------------------------------------------ processing

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def nudge(self, conversation_id: uuid.UUID) -> None:
        running = self._draining.get(conversation_id)
        if running is None or running.done():
            self._draining[conversation_id] = self._spawn(self.drain(conversation_id))

    async def drain(self, conversation_id: uuid.UUID) -> None:
        """Process this conversation's pending events in (received_at, id) order."""
        while True:
            async with self.db.transaction() as session:
                event = (
                    await session.execute(
                        select(InboundEvent)
                        .where(
                            InboundEvent.conversation_id == conversation_id,
                            InboundEvent.status == "pending",
                        )
                        .order_by(InboundEvent.received_at, InboundEvent.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if event is None:
                    return
                event.status = "processing"
                event.attempts += 1
                event_id, kind, payload = event.id, event.kind, dict(event.payload)
            try:
                await self._process(conversation_id, event_id, kind, payload)
                outcome, error = "done", None
            except (TurnInProgressError, RunnerOverloadedError):  # retried, in order
                outcome, error = "pending", None
                await asyncio.sleep(1)
            except Exception as exc:  # recorded on the event; retried up to MAX_ATTEMPTS_IN
                log.exception("whatsapp.processing_failed", event_id=event_id)
                outcome, error = "failed", type(exc).__name__
            async with self.db.transaction() as session:
                row = await session.get(InboundEvent, event_id)
                assert row is not None
                if outcome == "failed" and row.attempts < MAX_ATTEMPTS_IN:
                    outcome = "pending"
                row.status = outcome
                row.last_error = error
                row.processed_at = datetime.now(UTC) if outcome == "done" else None
            if outcome == "pending" and error:
                await asyncio.sleep(2)

    async def _process(
        self, conversation_id: uuid.UUID, event_id: int, kind: str, payload: dict[str, Any]
    ) -> None:
        text = str(payload.get("text") or "")
        if text.strip().lower() in STOP_WORDS:
            await self._opt_out(conversation_id)
            return
        # Any other message from the customer lifts an earlier STOP: the confirmation says
        # "no more messages unless you write again".
        await self._clear_opt_out(conversation_id)
        if kind == "unsupported":
            await self._reply_template(conversation_id, "unsupported_content")
            return
        client_message_id = f"wa:{event_id}"
        if await self.runner.replay(conversation_id, client_message_id) is not None:
            # The turn completed before a crash kept the event from being marked done
            # (review 02, finding 6): do not run it again.
            return
        clean = sanitize(text, source="customer")
        # The text was redacted at acceptance; keep the counts recorded then, so the notices and
        # sensitive-only routing work as on the web (review 02, finding 20).
        stored = {str(k): int(v) for k, v in (payload.get("redactions") or {}).items()}
        if stored:
            kinds = set(stored) | set(clean.redactions)
            merged = {k: max(stored.get(k, 0), clean.redactions.get(k, 0)) for k in kinds}
            clean = dataclasses.replace(clean, redactions=merged)
        await self.runner.execute(
            await self.runner.begin(
                conversation_id, clean, client_message_id, action=payload.get("button_id")
            )
        )

    async def _reply_template(self, conversation_id: uuid.UUID, key: str) -> None:
        text = self.templates.text(key, "whatsapp", "en")
        async with self.db.transaction() as session:
            message = Message(conversation_id=conversation_id, role="assistant", content=text)
            session.add(message)
            await session.flush()
            session.add(
                OutboundMessage(
                    conversation_id=conversation_id,
                    channel="whatsapp",
                    message_id=message.id,
                    kind="text",
                    payload={"text": text},
                )
            )
        self.nudge_sender()

    async def _opt_out(self, conversation_id: uuid.UUID) -> None:
        async with self.db.transaction() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            customer = await session.get(Customer, conversation.customer_id)
            assert customer is not None
            # Database time, like `outbound_messages.created_at`, which it is compared with: a
            # Python timestamp from another clock could make the confirmation look older than
            # the opt-out and suppress it.
            customer.whatsapp_opted_out_at = func.now()
            customer.marketing_opt_in = False
        await self._reply_template(conversation_id, "opt_out_confirmed")

    async def _clear_opt_out(self, conversation_id: uuid.UUID) -> None:
        async with self.db.transaction() as session:
            conversation = await session.get(Conversation, conversation_id)
            customer = (
                await session.get(Customer, conversation.customer_id) if conversation else None
            )
            if customer is not None and customer.whatsapp_opted_out_at is not None:
                customer.whatsapp_opted_out_at = None

    # ------------------------------------------------------------------ outbound

    async def deliver(
        self, conversation_id: uuid.UUID, text: str, metadata: dict[str, Any]
    ) -> None:
        """Record a delivery intent (runner hook). Free-form inside the window, else a template."""
        async with self.db.transaction() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.channel != "whatsapp":
                return
            customer = await session.get(Customer, conversation.customer_id)
            if customer is not None and customer.whatsapp_opted_out_at is not None:
                # STOP: nothing more is sent until the customer writes again (review 02, #13).
                log.info("whatsapp.suppressed_opted_out", conversation_id=str(conversation_id))
                return
            if window_open(conversation):
                body = render_for_whatsapp(text, metadata)
                actions = [a for a in metadata.get("actions") or []][:3]
                if actions:
                    payload: dict[str, Any] = {
                        "body": body[:1024],
                        "buttons": [
                            {"id": a["id"][:256], "title": a["label"][:20]} for a in actions
                        ],
                    }
                    session.add(
                        OutboundMessage(
                            conversation_id=conversation_id,
                            channel="whatsapp",
                            kind="interactive",
                            payload=payload,
                        )
                    )
                else:
                    for part in split_text(body):
                        session.add(
                            OutboundMessage(
                                conversation_id=conversation_id,
                                channel="whatsapp",
                                kind="text",
                                payload={"text": part},
                            )
                        )
            else:
                template = self.templates.get("application_update", "whatsapp", conversation.locale)
                session.add(
                    OutboundMessage(
                        conversation_id=conversation_id,
                        channel="whatsapp",
                        kind="template",
                        payload={
                            "name": template.meta_template_name,
                            "language": template.meta_language_code,
                            "params": [
                                (customer.display_name if customer else None) or "there",
                                text[:900],
                            ],
                            "body": template.render(
                                customer_name=(customer.display_name if customer else None)
                                or "there",
                                status_text=text[:900],
                            ),
                        },
                    )
                )
        self.nudge_sender()

    def nudge_sender(self) -> None:
        # Set before checking the task: a loop that is about to exit sees it and runs once more,
        # so an intent committed just after the loop's last query is never left behind.
        self._send_requested = True
        if self._sending is None or self._sending.done():
            self._sending = self._spawn(self.send_pending())

    async def send_pending(self) -> None:
        while True:
            self._send_requested = False
            async with self.db.transaction() as session:
                rows = (
                    (
                        await session.execute(
                            select(OutboundMessage)
                            .where(
                                OutboundMessage.channel == "whatsapp",
                                OutboundMessage.status == "pending",
                                # Database clock: next_attempt_at defaults to now() there.
                                OutboundMessage.next_attempt_at <= func.clock_timestamp(),
                            )
                            .order_by(OutboundMessage.created_at)
                            .limit(10)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    next_due = await session.scalar(
                        select(func.min(OutboundMessage.next_attempt_at)).where(
                            OutboundMessage.channel == "whatsapp",
                            OutboundMessage.status == "pending",
                        )
                    )
                    if next_due is not None:
                        self._wake_at(next_due)
                    if not self._send_requested:
                        return
                    continue  # nudged while finishing: look again
                for row in rows:
                    await self._send_one(session, row)
            await asyncio.sleep(0)

    async def _send_one(self, session: AsyncSession, row: OutboundMessage) -> None:
        conversation = await session.get(Conversation, row.conversation_id)
        if conversation is None:
            row.status, row.last_error = "failed", "conversation_missing"
            return
        customer = await session.get(Customer, conversation.customer_id)
        opted_out = customer.whatsapp_opted_out_at if customer else None
        if opted_out is not None and row.created_at < opted_out:
            row.status, row.last_error = "failed", "opted_out"  # queued before STOP: never sent
            return
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.customer_id == conversation.customer_id,
                    ChannelIdentity.channel == "whatsapp",
                )
            )
        ).scalar_one_or_none()
        row.attempts += 1
        if identity is None:
            row.status, row.last_error = "failed", "no_whatsapp_identity"
            return
        try:
            provider_id = await self._transmit(session, row, identity.external_id)
            row.status, row.provider_message_id, row.last_error = "sent", provider_id, None
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429 or code >= 500:
                self._backoff(row, f"http_{code}")
            else:
                row.status, row.last_error = "failed", f"http_{code}"
        except httpx.HTTPError as exc:
            self._backoff(row, type(exc).__name__)

    def _backoff(self, row: OutboundMessage, error: str) -> None:
        if row.attempts >= MAX_ATTEMPTS_OUT:
            row.status, row.last_error = "failed", error
            return
        row.last_error = error
        row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2**row.attempts)

    async def _transmit(self, session: AsyncSession, row: OutboundMessage, to: str) -> str:
        if self.settings.whatsapp_mode == "simulator":
            session.add(
                SimulatorOutbox(
                    conversation_id=row.conversation_id,
                    to_external_id=to,
                    payload={"type": row.kind, **row.payload},
                )
            )
            return f"sim-{uuid.uuid4()}"
        url = (
            f"https://graph.facebook.com/{self.settings.whatsapp_graph_version}/"
            f"{self.settings.whatsapp_phone_number_id}/messages"
        )
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
        }
        if row.kind == "text":
            body |= {"type": "text", "text": {"body": row.payload["text"]}}
        elif row.kind == "interactive":
            body |= {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": row.payload["body"]},
                    "action": {
                        "buttons": [{"type": "reply", "reply": b} for b in row.payload["buttons"]]
                    },
                },
            }
        else:
            body |= {
                "type": "template",
                "template": {
                    "name": row.payload["name"],
                    "language": {"code": row.payload["language"]},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": p} for p in row.payload["params"]
                            ],
                        }
                    ],
                },
            }
        response = await self.http.post(
            url,
            content=json.dumps(body),
            headers={
                "Authorization": f"Bearer {self.settings.whatsapp_access_token.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return str(response.json()["messages"][0]["id"])

    async def recover(self) -> None:
        """At start-up: re-queue stuck events and resume pending sends."""
        cutoff = datetime.now(UTC) - timedelta(minutes=2)
        async with self.db.transaction() as session:
            rows = (
                (
                    await session.execute(
                        update(InboundEvent)
                        .where(
                            InboundEvent.status == "processing", InboundEvent.received_at < cutoff
                        )
                        .values(status="pending")
                        .returning(InboundEvent.conversation_id)
                    )
                )
                .scalars()
                .all()
            )
            pending = (
                (
                    await session.execute(
                        select(InboundEvent.conversation_id)
                        .where(InboundEvent.status == "pending")
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        for conversation_id in {*rows, *pending}:
            if conversation_id is not None:
                self.nudge(conversation_id)
        self.nudge_sender()

    def _wake_at(self, when: datetime) -> None:
        """Run the sender again when the earliest retry is due (review 02, finding 17)."""
        if self._wakeup is not None:
            self._wakeup.cancel()
        delay = max(0.5, (when - datetime.now(UTC)).total_seconds())
        self._wakeup = asyncio.get_running_loop().call_later(delay, self.nudge_sender)

    async def close(self) -> None:
        if self._wakeup is not None:
            self._wakeup.cancel()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=10)
        await self.http.aclose()
