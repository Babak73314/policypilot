"""Handoff and human review: a replay-safe interrupt and resume.

See docs/design/agent-design.md, section "Interrupts and resume".

`human_review` is re-executed from the start when resumed, so it only calls `interrupt()`.
Every side effect happens in `prepare_review` (before) and `finalize_review` (after), both
idempotent: keyed by a stable `review_key` and deterministic outbox event ids.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.agents import prompts
from policypilot.agents.context import AgentContext
from policypilot.agents.models import MODEL_FAILURES
from policypilot.agents.nodes import message_text, model_history
from policypilot.agents.state import ConversationState
from policypilot.db.models import Application, Conversation, Customer, Lead, ReviewTask
from policypilot.services.applications import freeze_snapshot
from policypilot.services.events import audit, enqueue_event

UNRESOLVED = ("open", "claimed", "resolving")


async def handoff(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    try:
        summary = await ctx.models.plain("writer").ainvoke(
            [
                SystemMessage(content=prompts.SUMMARISE_FOR_AGENT),
                *model_history(state["messages"], 8),
            ],
            config=ctx.llm_config(),
        )
        # Text blocks only: Claude's reply also carries thinking blocks with signatures, which
        # must never reach the agent desk (found in the first live run).
        content = message_text(summary)
    except MODEL_FAILURES:
        # A person must stay reachable when the models are not (review 02, finding 14).
        last = next(
            (str(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        content = f"Customer asked to talk to a person. Last message: {last[:300]}"
    return {"review_request": {"kind": "handoff", "summary": content.strip()[:1000]}}


async def _unresolved_task(session: AsyncSession, conversation_id: uuid.UUID) -> ReviewTask | None:
    return (
        await session.execute(
            select(ReviewTask).where(
                ReviewTask.conversation_id == conversation_id, ReviewTask.status.in_(UNRESOLVED)
            )
        )
    ).scalar_one_or_none()


async def prepare_review(
    state: ConversationState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    ctx = runtime.context
    app = dict(state.get("application") or {})
    is_application = bool(state.get("submit_application")) and app.get("application_id")
    kind = "application_review" if is_application else "handoff"
    if is_application:
        review_key = f"application:{app['application_id']}:v{app['snapshot_version']}"
        product = app.get("product_name", "a product")
        summary = f"Application for {product} (snapshot v{app['snapshot_version']})"
    else:
        review_key = f"handoff:{ctx.conversation_id}:{ctx.turn_id}"
        summary = str(
            (state.get("review_request") or {}).get("summary") or "Customer asked for an agent."
        )

    async with ctx.db.transaction() as session:
        existing = await _unresolved_task(session, ctx.conversation_id)
        if existing is None:
            await session.execute(
                insert(ReviewTask)
                .values(
                    review_key=review_key,
                    kind=kind,
                    conversation_id=ctx.conversation_id,
                    application_id=uuid.UUID(app["application_id"]) if is_application else None,
                    snapshot_version=app.get("snapshot_version") if is_application else None,
                    summary=summary,
                    priority=1 if kind == "handoff" else 0,
                    payload={"kind": kind, "summary": summary},
                )
                .on_conflict_do_nothing(index_elements=["review_key"])
            )
        task = (
            existing
            or (
                await session.execute(select(ReviewTask).where(ReviewTask.review_key == review_key))
            ).scalar_one()
        )
        if is_application:
            await session.execute(
                update(Application)
                .where(Application.id == uuid.UUID(app["application_id"]))
                .values(status="pending_review")
            )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == ctx.conversation_id)
            .values(status="awaiting_review" if kind == "application_review" else "human_takeover")
        )
        event = "application.submitted" if kind == "application_review" else "handoff.requested"
        data: dict[str, Any] = {
            "reviewTaskId": str(task.id),
            "conversationId": str(ctx.conversation_id),
            "channel": ctx.channel,
            "customerId": str(ctx.customer_id),
        }
        if is_application:
            data |= {"applicationId": app["application_id"], "productName": app.get("product_name")}
        else:
            data |= {"reason": "customer_request", "priority": "high"}
        await enqueue_event(
            session,
            event,
            data,
            key=f"{task.review_key}:submitted",
            links={"dashboard": f"/agent/reviews/{task.id}"},
        )
        audit(
            session,
            actor_type="assistant",
            action=f"review.{kind}.opened",
            entity_type="review_task",
            entity_id=str(task.id),
        )
        payload = {
            "kind": task.kind,
            "review_task_id": str(task.id),
            "application_id": app.get("application_id") if is_application else None,
            "snapshot_version": app.get("snapshot_version") if is_application else None,
            "summary": task.summary,
        }
    template = "app_submitted" if kind == "application_review" else "handoff_started"
    return {
        "review": payload,
        "draft": ctx.templates.text(template, ctx.channel, ctx.locale),
        "templated": True,
    }


async def human_review(state: ConversationState) -> dict[str, Any]:
    """Pause for a human. No other side effects: this node runs again on resume."""
    review = dict(state.get("review") or {})
    decision = interrupt(review)
    return {"review": {**review, "decision": decision}}


DECISION_TEMPLATES = {
    "approve": "review_decision_approved",
    "edit_and_approve": "review_decision_approved",
    "request_changes": "review_decision_changes",
    "reject": "review_decision_rejected",
    "withdraw": "review_withdrawn",
    "expire": "review_expired",
    "return_to_assistant": "handoff_returned",
    "close": "handoff_returned",
}
APPLICATION_STATUS = {
    "approve": "approved",
    "edit_and_approve": "approved",
    "request_changes": "changes_requested",
    "reject": "rejected",
    "withdraw": "withdrawn",
    "expire": "expired",
}
RESOLUTION = {
    "approve": "approved",
    "edit_and_approve": "approved",
    "request_changes": "changes_requested",
    "reject": "rejected",
    "withdraw": "withdrawn",
    "expire": "expired",
    "return_to_assistant": "returned",
    "close": "closed",
}


async def finalize_review(
    state: ConversationState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    ctx = runtime.context
    review = dict(state.get("review") or {})
    decision = dict(review.get("decision") or {})
    action = str(decision.get("action"))
    task_id = uuid.UUID(review["review_task_id"])
    app = dict(state.get("application") or {})
    now = datetime.now(UTC)

    async with ctx.db.transaction() as session:
        task = await session.get(ReviewTask, task_id, with_for_update=True)
        assert task is not None
        stored = dict(task.decision or {})  # clerical values and reviewer live here, not in state
        if task.status != "resolved":
            if task.kind == "application_review" and task.application_id is not None:
                record = await session.get(Application, task.application_id)
                assert record is not None
                if action == "edit_and_approve" and stored.get("clerical_edits"):
                    customer = await session.get(Customer, record.customer_id)
                    assert customer is not None
                    await freeze_snapshot(
                        session,
                        record,
                        customer=customer,
                        needs_profile=dict(state.get("profile") or {}),
                        created_by=f"agent:{decision.get('agent_id')}",
                        edits=stored.get("clerical_edits"),
                    )
                record.status = APPLICATION_STATUS[action]
                if action in ("approve", "edit_and_approve"):
                    await session.execute(
                        update(Lead).where(Lead.id == record.lead_id).values(status="qualified")
                    )
                await enqueue_event(
                    session,
                    "application.reviewed",
                    {
                        "applicationId": str(record.id),
                        "decision": RESOLUTION[action],
                        "reviewedBy": stored.get("agent_email"),
                        "customerId": str(record.customer_id),
                    },
                    key=f"{task.review_key}:resolved",
                )
            else:
                await enqueue_event(
                    session,
                    "handoff.resolved",
                    {
                        "reviewTaskId": str(task.id),
                        "resolution": RESOLUTION[action],
                        "customerId": str(ctx.customer_id),
                    },
                    key=f"{task.review_key}:resolved",
                )
            task.status = "resolved"
            task.resolution = RESOLUTION[action]
            task.resolved_at = now
            if decision.get("agent_id"):
                task.resolved_by = uuid.UUID(str(decision["agent_id"]))
            closing = action == "close"
            await session.execute(
                update(Conversation)
                .where(Conversation.id == ctx.conversation_id)
                .values(
                    status="closed" if closing else "active",
                    closed_at=now if closing else None,
                    close_reason="agent_closed" if closing else None,
                )
            )
            audit(
                session,
                actor_type="agent" if decision.get("agent_id") else "system",
                actor_id=str(decision.get("agent_id") or "") or None,
                action=f"review.{RESOLUTION[action]}",
                entity_type="review_task",
                entity_id=str(task.id),
            )

    intervening: list[Any] = []
    for message in decision.get("intervening") or []:
        if message.get("role") == "agent":
            intervening.append(AIMessage(content=message["content"], name="agent"))
        else:
            intervening.append(HumanMessage(content=message["content"]))
    note = str(decision.get("notes") or "").strip()
    template = DECISION_TEMPLATES[action]
    params = {"note": note} if "note" in ctx.templates.get(template, ctx.channel).params else {}
    new_app: dict[str, Any] | None = None
    if action == "request_changes":
        new_app = {
            **app,
            "step": "confirm_submit",
            "summary_shown": False,
            "status": "changes_requested",
        }
    return {
        "messages": intervening,
        "draft": ctx.templates.text(template, ctx.channel, ctx.locale, **params).strip(),
        "templated": True,
        "application": new_app,
        "review": None,
        "submit_application": False,
        "review_request": None,
    }
