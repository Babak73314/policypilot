"""The turn runner: idempotent turn registration, the conversation lock, graph execution, resume
after human review, and storage of the validated reply
(docs/design/agent-design.md#where-the-graph-sits-in-a-turn).

Web and WhatsApp use the same runner. Callers `begin()` a turn, which raises
`TurnInProgressError` immediately if the conversation is busy, then `execute()` it; the lock is
held until `execute()` returns, even if the HTTP client has gone away.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import select, update

from policypilot.agents.context import AgentContext
from policypilot.agents.models import TRANSIENT_ERRORS, ModelBudgetExceededError, ModelRoles
from policypilot.config import Settings
from policypilot.db.models import Conversation, Message, ReviewTask, Turn
from policypilot.db.session import Database
from policypilot.observability.tracer import RunTrace
from policypilot.rag.embeddings import Embedder
from policypilot.security.ingress import SanitizedText, sanitize
from policypilot.services import conversations
from policypilot.services.events import audit, nudge_dispatcher
from policypilot.services.locks import ConversationBusyError, conversation_lock
from policypilot.services.templates import TemplateStore

log = structlog.get_logger(__name__)
TURN_DEADLINE_SECONDS = 90
ADMISSION_WAIT_SECONDS = 2.0  # then 503 with Retry-After (api.md)
ACK_INTERVAL = timedelta(minutes=10)
WITHDRAW = re.compile(r"\b(withdraw|cancel (?:my |the )?application)\b", re.I)
ESCALATE = re.compile(r"\b(agent|human|real person|speak to someone)\b", re.I)
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
# Delivers a stored reply on an external channel (WhatsApp); set when that channel is wired.
Deliver = Callable[[uuid.UUID, str, dict[str, Any]], Awaitable[None]]


class RunnerOverloadedError(RuntimeError):
    """Too many turns are executing on this process; retry shortly (HTTP 503)."""


class TurnInProgressError(RuntimeError):
    pass


class ConversationNotFoundError(LookupError):
    pass


@dataclass
class PreparedTurn:
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    channel: str
    locale: str
    status: str
    turn_id: uuid.UUID
    text: SanitizedText
    action: str | None
    stack: AsyncExitStack


@dataclass
class TurnResult:
    message: dict[str, Any] | None
    conversation_status: str
    run_id: uuid.UUID | None = None
    interrupted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


async def _noop(event: str, data: dict[str, Any]) -> None:
    return None


class TurnRunner:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        models: ModelRoles,
        embedder: Embedder,
        templates: TemplateStore,
    ) -> None:
        self.db = db
        self.settings = settings
        self.graph = graph
        self.models = models
        self.embedder = embedder
        self.templates = templates
        # Each executing turn holds one pooled connection for its conversation lock and needs more
        # for its work, so at most half the pool may execute at once (review 02, finding 8).
        self.semaphore = asyncio.Semaphore(max(1, settings.db_pool_max // 2))
        self.deliver: Deliver | None = None

    # ------------------------------------------------------------------ begin

    async def replay(self, conversation_id: uuid.UUID, client_message_id: str) -> TurnResult | None:
        """The stored result of a completed turn with this client message id, if any."""
        async with self.db.session() as session:
            turn = await conversations.find_turn(session, conversation_id, client_message_id)
            # A `running` turn may be executing or abandoned by a crash: `begin` decides under the
            # conversation lock (review 02, finding 5).
            if turn is None or turn.status in ("failed", "running"):
                return None
            conversation = await conversations.get_conversation(session, conversation_id)
            reply = (
                await session.get(Message, turn.assistant_message_id)
                if turn.assistant_message_id
                else None
            )
        if conversation is None:
            return None
        return TurnResult(
            message=conversations.message_view(reply) if reply else None,
            conversation_status=conversation.status,
        )

    async def begin(
        self,
        conversation_id: uuid.UUID,
        text: SanitizedText,
        client_message_id: str,
        *,
        action: str | None = None,
    ) -> PreparedTurn:
        stack = AsyncExitStack()
        try:
            # Admission before the lock: a queued turn must not hold a pooled lock connection.
            await stack.enter_async_context(self._admission())
            await stack.enter_async_context(conversation_lock(self.db, conversation_id))
        except ConversationBusyError as exc:
            await stack.aclose()
            raise TurnInProgressError(str(conversation_id)) from exc
        except BaseException:
            await stack.aclose()
            raise
        try:
            async with self.db.transaction() as session:
                conversation = await conversations.get_conversation(session, conversation_id)
                if conversation is None or conversation.status == "closed":
                    raise ConversationNotFoundError(str(conversation_id))
                # We hold the lock, so nothing is executing here: a `running` turn was abandoned
                # (process crash or cancellation) and becomes retryable with the same id.
                await session.execute(
                    update(Turn)
                    .where(Turn.conversation_id == conversation_id, Turn.status == "running")
                    .values(status="failed", error_code="abandoned")
                )
                existing = await conversations.find_turn(
                    session, conversation_id, client_message_id
                )
                if existing is not None and existing.status != "failed":
                    raise TurnInProgressError(str(conversation_id))
                turn, _ = await conversations.register_turn(
                    session, conversation, client_message_id, text, existing=existing
                )
                return PreparedTurn(
                    conversation_id=conversation.id,
                    customer_id=conversation.customer_id,
                    channel=conversation.channel,
                    locale=conversation.locale,
                    status=conversation.status,
                    turn_id=turn.id,
                    text=text,
                    action=action,
                    stack=stack,
                )
        except BaseException:
            await stack.aclose()
            raise

    # ------------------------------------------------------------------ execute a customer turn

    @asynccontextmanager
    async def _admission(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=ADMISSION_WAIT_SECONDS)
        except TimeoutError as exc:
            raise RunnerOverloadedError("too many concurrent turns") from exc
        try:
            yield
        finally:
            self.semaphore.release()

    async def execute(self, prepared: PreparedTurn, emit: Emit = _noop) -> TurnResult:
        try:
            return await self._execute(prepared, emit)
        finally:
            await prepared.stack.aclose()
            nudge_dispatcher()

    def _context(
        self,
        *,
        conversation_id: uuid.UUID,
        customer_id: uuid.UUID,
        channel: str,
        locale: str,
        turn_id: uuid.UUID,
        trace: RunTrace,
        text: SanitizedText | None,
    ) -> AgentContext:
        return AgentContext(
            db=self.db,
            settings=self.settings,
            models=self.models,
            embedder=self.embedder,
            templates=self.templates,
            conversation_id=conversation_id,
            customer_id=customer_id,
            channel=channel,
            locale=locale,
            turn_id=turn_id,
            trace=trace,
            redactions=dict(text.redactions) if text else {},
            stored_text=text.stored_text if text else "",
        )

    async def _execute(self, prepared: PreparedTurn, emit: Emit) -> TurnResult:
        trace = RunTrace(prepared.conversation_id, prepared.channel, prepared.turn_id)
        await emit("turn.started", {"turnId": str(prepared.turn_id), "runId": str(trace.id)})
        if prepared.status in ("awaiting_review", "human_takeover"):
            return await self._paused(prepared, trace, emit)
        ctx = self._context(
            conversation_id=prepared.conversation_id,
            customer_id=prepared.customer_id,
            channel=prepared.channel,
            locale=prepared.locale,
            turn_id=prepared.turn_id,
            trace=trace,
            text=prepared.text,
        )
        graph_input = {
            "messages": [HumanMessage(content=prepared.text.model_text)],
            "action": prepared.action,
        }
        return await self._run_graph(graph_input, ctx, emit, turn_id=prepared.turn_id)

    async def _run_graph(
        self, graph_input: Any, ctx: AgentContext, emit: Emit, *, turn_id: uuid.UUID | None
    ) -> TurnResult:
        config: RunnableConfig = {"configurable": {"thread_id": str(ctx.conversation_id)}}
        trace = ctx.trace
        started = time.monotonic()
        try:
            async with asyncio.timeout(TURN_DEADLINE_SECONDS):
                async for mode, chunk in self.graph.astream(
                    graph_input, config, context=ctx, stream_mode=["custom", "updates"]
                ):
                    if mode == "custom":
                        await self._forward(chunk, emit)
            snapshot = await self.graph.aget_state(config)
        except ModelBudgetExceededError:
            trace.guardrail("model_budget_exhausted")
            return await self._terminal(ctx, turn_id, emit, "rate_limited")
        except (TimeoutError, *TRANSIENT_ERRORS) as exc:  # type: ignore[misc]
            trace.guardrail("assistant_unavailable", type(exc).__name__)
            return await self._terminal(ctx, turn_id, emit, "assistant_unavailable")
        except Exception as exc:
            log.exception("turn.failed", conversation_id=str(ctx.conversation_id))
            await self._fail(turn_id, trace, exc)
            await emit(
                "error",
                {
                    "code": "turn_failed",
                    "message": "The assistant could not answer.",
                    "retryable": True,
                },
            )
            raise
        trace.step("node", "graph", started)
        values = snapshot.values
        interrupted = bool(getattr(snapshot, "interrupts", ()))
        # A run that paused for human review ends before guard_output; its last words are the
        # fixed acknowledgement template written by prepare_review.
        reply = (values.get("draft") if interrupted else values.get("reply")) or ""
        metadata = {
            "citations": [] if interrupted else values.get("citations") or [],
            "artifacts": values.get("artifacts") or [],
            "quoteIds": values.get("turn_quote_ids") or [],
            "actions": [] if interrupted else values.get("actions") or [],
            "runId": str(trace.id),
        }
        result = await self._store_reply(
            ctx.conversation_id,
            turn_id,
            trace,
            reply,
            metadata,
            status="interrupted" if interrupted else "ok",
            channel=ctx.channel,
        )
        result.interrupted = interrupted
        await self._emit_reply(result, emit)
        return result

    async def _forward(self, chunk: Any, emit: Emit) -> None:
        if not isinstance(chunk, dict):
            return
        if chunk.get("type") == "status":
            await emit("status", {"key": chunk.get("key"), "label": chunk.get("label")})
        elif chunk.get("type") == "artifact":
            artifact = chunk.get("artifact") or {}
            await emit(f"artifact.{artifact.get('type', 'unknown')}", artifact)

    # ------------------------------------------------------------------ resume after review

    async def resume(
        self,
        conversation_id: uuid.UUID,
        decision: dict[str, Any],
        *,
        lock_held: bool = False,
        lock_wait_seconds: float = 10.0,
    ) -> TurnResult:
        """Resume an interrupted thread with a human decision (`Command(resume=...)`)."""
        async with AsyncExitStack() as stack:
            if not lock_held:
                try:
                    await stack.enter_async_context(
                        conversation_lock(self.db, conversation_id, wait_seconds=lock_wait_seconds)
                    )
                except ConversationBusyError as exc:
                    raise TurnInProgressError(str(conversation_id)) from exc
            async with self.db.session() as session:
                conversation = await conversations.get_conversation(session, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            ctx = self._context(
                conversation_id=conversation_id,
                customer_id=conversation.customer_id,
                channel=conversation.channel,
                locale=conversation.locale,
                turn_id=uuid.uuid4(),
                trace=RunTrace(conversation_id, conversation.channel, None),
                text=None,
            )
            result = await self._run_graph(Command(resume=decision), ctx, _noop, turn_id=None)
        nudge_dispatcher()
        return result

    async def is_interrupted(self, conversation_id: uuid.UUID) -> bool:
        snapshot = await self.graph.aget_state(
            {"configurable": {"thread_id": str(conversation_id)}}
        )
        return bool(getattr(snapshot, "interrupts", ()))

    # ------------------------------------------------------------------ paused conversations

    async def _paused(self, prepared: PreparedTurn, trace: RunTrace, emit: Emit) -> TurnResult:
        """Control precedence while a human owns the thread (agent-design.md, paused threads)."""
        text = prepared.text.stored_text
        withdraw = escalate = False
        async with self.db.transaction() as session:
            task = (
                await session.execute(
                    select(ReviewTask).where(
                        ReviewTask.conversation_id == prepared.conversation_id,
                        ReviewTask.status.in_(("open", "claimed", "resolving")),
                    )
                )
            ).scalar_one_or_none()
            awaiting = prepared.status == "awaiting_review" and task is not None
            if awaiting and task is not None and WITHDRAW.search(text):
                if task.status in ("open", "claimed"):
                    task.status = "resolving"
                    task.decision = {"action": "withdraw", "by": "customer"}
                    audit(
                        session,
                        actor_type="customer",
                        action="review.withdraw_requested",
                        entity_type="review_task",
                        entity_id=str(task.id),
                    )
                    withdraw = True
            elif awaiting and task is not None and ESCALATE.search(text):
                task.priority = max(task.priority, 1)
                audit(
                    session,
                    actor_type="customer",
                    action="review.escalated",
                    entity_type="review_task",
                    entity_id=str(task.id),
                )
                escalate = True
        if withdraw:
            intervening = await self.intervening(prepared.conversation_id, task)
            result = await self.resume(
                prepared.conversation_id,
                {"action": "withdraw", "intervening": intervening},
                lock_held=True,
            )
            await self._complete_turn(prepared.turn_id, result)
            await self._emit_reply(result, emit)
            return result
        if escalate:
            reply = self.templates.text("review_escalated", prepared.channel, prepared.locale)
        elif prepared.status == "awaiting_review" and not await self._acked_recently(prepared):
            reply = self.templates.text("review_pending_ack", prepared.channel, prepared.locale)
        else:
            reply = ""  # human takeover, or acknowledged recently: the agent sees the message
        if reply:
            result = await self._store_reply(
                prepared.conversation_id,
                prepared.turn_id,
                trace,
                reply,
                {},
                status="ok",
                channel=prepared.channel,
            )
        else:
            await self._complete_turn(prepared.turn_id, None)
            result = TurnResult(message=None, conversation_status=prepared.status)
        await self._emit_reply(result, emit)
        return result

    async def _acked_recently(self, prepared: PreparedTurn) -> bool:
        ack = self.templates.text("review_pending_ack", prepared.channel, prepared.locale)
        cutoff = datetime.now(UTC) - ACK_INTERVAL
        async with self.db.session() as session:
            found = (
                await session.execute(
                    select(Message.id)
                    .where(
                        Message.conversation_id == prepared.conversation_id,
                        Message.role == "assistant",
                        Message.content == ack,
                        Message.created_at > cutoff,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        return found is not None

    async def intervening(
        self, conversation_id: uuid.UUID, task: ReviewTask | None
    ) -> list[dict[str, str]]:
        """Customer and agent messages stored since the review opened, as model-safe text."""
        if task is None:
            return []
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.created_at > task.created_at,
                        Message.role.in_(("customer", "agent")),
                    )
                    .order_by(Message.id)
                    .limit(30)
                )
            ).scalars()
            return [
                {"role": m.role, "content": sanitize(m.content, source="agent").model_text}
                for m in rows
            ]

    # ------------------------------------------------------------------ storage and delivery

    async def _store_reply(
        self,
        conversation_id: uuid.UUID,
        turn_id: uuid.UUID | None,
        trace: RunTrace,
        reply: str,
        metadata: dict[str, Any],
        *,
        status: str,
        channel: str,
    ) -> TurnResult:
        async with self.db.transaction() as session:
            message = await conversations.store_assistant_message(
                session, conversation_id, turn_id, reply, metadata
            )
            if turn_id is not None:
                await session.execute(
                    update(Turn)
                    .where(Turn.id == turn_id)
                    .values(
                        status="completed",
                        assistant_message_id=message.id,
                        run_id=trace.id,
                        finished_at=datetime.now(UTC),
                    )
                )
            conversation = await session.get(Conversation, conversation_id)
            view = conversations.message_view(message)
            conversation_status = conversation.status if conversation else "active"
        await trace.persist(self.db, status)
        if self.deliver is not None and channel != "web" and reply:
            await self.deliver(conversation_id, reply, metadata)
        return TurnResult(message=view, conversation_status=conversation_status, run_id=trace.id)

    async def _complete_turn(self, turn_id: uuid.UUID, result: TurnResult | None) -> None:
        message_id = (result.message or {}).get("id") if result else None
        async with self.db.transaction() as session:
            await session.execute(
                update(Turn)
                .where(Turn.id == turn_id)
                .values(
                    status="completed",
                    assistant_message_id=message_id,
                    finished_at=datetime.now(UTC),
                )
            )

    async def _emit_reply(self, result: TurnResult, emit: Emit) -> None:
        message = result.message or {}
        text = str(message.get("content") or "")
        for start in range(0, len(text), 48):
            await emit("message.delta", {"text": text[start : start + 48]})
        quote_ids = [
            a.get("quoteId") for a in message.get("artifacts", []) if a.get("type") == "quote"
        ]
        await emit(
            "message.completed",
            {
                "messageId": message.get("id"),
                "text": text,
                "citations": message.get("citations", []),
                "quoteIds": quote_ids,
                "actions": message.get("actions", []),
                "conversationStatus": result.conversation_status,
            },
        )

    async def _terminal(
        self, ctx: AgentContext, turn_id: uuid.UUID | None, emit: Emit, template: str
    ) -> TurnResult:
        reply = self.templates.text(template, ctx.channel, ctx.locale)
        result = await self._store_reply(
            ctx.conversation_id,
            turn_id,
            ctx.trace,
            reply,
            {"runId": str(ctx.trace.id)},
            status="error",
            channel=ctx.channel,
        )
        await self._emit_reply(result, emit)
        return result

    async def _fail(self, turn_id: uuid.UUID | None, trace: RunTrace, exc: BaseException) -> None:
        if turn_id is not None:
            async with self.db.transaction() as session:
                await session.execute(
                    update(Turn)
                    .where(Turn.id == turn_id)
                    .values(
                        status="failed",
                        error_code=type(exc).__name__,
                        finished_at=datetime.now(UTC),
                    )
                )
        await trace.persist(self.db, "error", error=type(exc).__name__)
