"""Agent dashboard API: login, review queue, conversations with traces, leads, catalog, metrics.

Every endpoint except login needs an agent JWT (ADR-0014). Writes are audit-logged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select, text, update

from policypilot.agents.runner import TurnInProgressError, TurnResult
from policypilot.api.state import AppState, app_state, client_ip
from policypilot.db.models import (
    AgentRun,
    AgentRunStep,
    AgentUser,
    Application,
    ApplicationSnapshot,
    Conversation,
    Customer,
    Lead,
    Message,
    OutboxEvent,
    Product,
    ProductText,
    Quote,
    RateTable,
    ReviewTask,
)
from policypilot.security.auth import (
    InvalidTokenError,
    issue_agent_token,
    verify_agent_token,
    verify_password,
)
from policypilot.security.ingress import sanitize
from policypilot.services import conversations
from policypilot.services.catalog import list_products
from policypilot.services.events import audit
from policypilot.services.locks import ConversationBusyError, conversation_lock
from policypilot.services.quoting import quote_artifact
from policypilot.services.reviews import Actor, ReviewError, claim, resolve, retry_resume

router = APIRouter(prefix="/api/v1", tags=["agent"])


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class LoginRequest(_Camel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class ResolveRequest(_Camel):
    action: str
    notes: str | None = Field(default=None, max_length=1000)
    clerical_edits: dict[str, str] | None = None


class AgentMessage(_Camel):
    text: str = Field(min_length=1, max_length=2000)


class LeadUpdate(_Camel):
    status: str | None = Field(default=None, pattern="^(new|contacted|qualified|won|lost)$")
    next_follow_up_at: datetime | None = None


# --------------------------------------------------------------------------- auth


async def current_agent(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> tuple[AppState, AgentUser]:
    state = app_state(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail={"code": "missing_token", "message": "Login required."})
    try:
        claims = verify_agent_token(
            state.settings.app_secret_key.get_secret_value(), authorization[7:].strip()
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            401, detail={"code": "invalid_token", "message": "Login again."}
        ) from exc
    async with state.db.session() as session:
        agent = await session.get(AgentUser, claims.agent_id)
    if agent is None or not agent.active:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Login again."})
    return state, agent


Agent = Annotated[tuple[AppState, AgentUser], Depends(current_agent)]


def _agent_view(agent: AgentUser) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "email": agent.email,
        "displayName": agent.display_name,
        "role": agent.role,
    }


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    state = app_state(request)
    email = body.email.strip().lower()
    state.limiter.check(f"login-ip:{client_ip(request)}", 5, 60)
    state.limiter.check(f"login-email:{email}", 5, 60)
    async with state.db.transaction() as session:
        agent = (
            await session.execute(select(AgentUser).where(func.lower(AgentUser.email) == email))
        ).scalar_one_or_none()
        if (
            agent is None
            or not agent.active
            or not verify_password(agent.password_hash, body.password)
        ):
            audit(
                session,
                actor_type="agent",
                action="agent.login_failed",
                entity_type="agent",
                entity_id=None,
                details={"ip": client_ip(request)},
            )
            raise HTTPException(
                401, detail={"code": "invalid_credentials", "message": "Wrong email or password."}
            )
        agent.last_login_at = datetime.now(UTC)
        audit(
            session,
            actor_type="agent",
            actor_id=str(agent.id),
            action="agent.login",
            entity_type="agent",
            entity_id=str(agent.id),
        )
        token, expires = issue_agent_token(
            state.settings.app_secret_key.get_secret_value(),
            agent.id,
            agent.role,
            state.settings.agent_token_ttl_minutes,
        )
        return {"accessToken": token, "expiresAt": expires.isoformat(), "agent": _agent_view(agent)}


@router.get("/auth/me")
async def me(agent: Agent) -> dict[str, Any]:
    return _agent_view(agent[1])


# --------------------------------------------------------------------------- reviews


async def _task_summary(session: Any, task: ReviewTask) -> dict[str, Any]:
    conversation = await session.get(Conversation, task.conversation_id)
    claimed = await session.get(AgentUser, task.claimed_by) if task.claimed_by else None
    return {
        "id": str(task.id),
        "kind": task.kind,
        "status": task.status,
        "priority": task.priority,
        "summary": task.summary,
        "conversationId": str(task.conversation_id),
        "channel": conversation.channel if conversation else "web",
        "applicationId": str(task.application_id) if task.application_id else None,
        "createdAt": task.created_at.isoformat(),
        "claimedBy": {"id": str(claimed.id), "displayName": claimed.display_name}
        if claimed
        else None,
    }


async def _task_detail(state: AppState, task_id: uuid.UUID) -> dict[str, Any]:
    async with state.db.session() as session:
        task = await session.get(ReviewTask, task_id)
        if task is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "No such review."})
        view = await _task_summary(session, task)
        snapshot_view = None
        if task.application_id is not None:
            application = await session.get(Application, task.application_id)
            assert application is not None
            snapshot = await session.get(
                ApplicationSnapshot, (application.id, application.current_snapshot_version)
            )
            if snapshot is not None:
                quote = await session.get(Quote, snapshot.quote_id)
                products = await list_products(
                    session, product_ids=[application.product_id], active_only=False
                )
                snapshot_view = {
                    "version": snapshot.version,
                    "data": snapshot.data,
                    "needsProfile": snapshot.needs_profile,
                    "quote": quote_artifact(quote, products[0], locale="en")
                    if quote and products
                    else None,
                }
        messages = await session.execute(
            select(Message)
            .where(
                Message.conversation_id == task.conversation_id,
                Message.created_at >= task.created_at,
            )
            .order_by(Message.id)
        )
        conversation = await session.get(Conversation, task.conversation_id)
        window_open = None
        if conversation is not None and conversation.channel == "whatsapp":
            last = conversation.last_customer_message_at
            window_open = bool(last and (datetime.now(UTC) - last).total_seconds() < 23.9 * 3600)
        return view | {
            "snapshot": snapshot_view,
            "messagesSinceOpened": [conversations.message_view(m) for m in messages.scalars()],
            "decision": task.decision,
            "resolution": task.resolution,
            "resolvedAt": task.resolved_at.isoformat() if task.resolved_at else None,
            "whatsappWindowOpen": window_open,
        }


@router.get("/agent/review-tasks")
async def list_reviews(
    agent: Agent,
    status: Annotated[str | None, Query(pattern="^(open|claimed|resolving|resolved)$")] = None,
    kind: Annotated[str | None, Query(pattern="^(application_review|handoff)$")] = None,
) -> dict[str, Any]:
    state, _ = agent
    stmt = select(ReviewTask).order_by(ReviewTask.priority.desc(), ReviewTask.created_at.desc())
    if status:
        stmt = stmt.where(ReviewTask.status == status)
    if kind:
        stmt = stmt.where(ReviewTask.kind == kind)
    async with state.db.session() as session:
        tasks = (await session.execute(stmt.limit(100))).scalars().all()
        items = [await _task_summary(session, t) for t in tasks]
    return {"items": items, "nextCursor": None}


@router.get("/agent/review-tasks/{task_id}")
async def get_review(task_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    return await _task_detail(agent[0], task_id)


def _review_error(exc: ReviewError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


@router.post("/agent/review-tasks/{task_id}/claim")
async def claim_review(task_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, user = agent
    try:
        await claim(state.db, task_id, Actor(agent_id=user.id, email=user.email))
    except ReviewError as exc:
        raise _review_error(exc) from exc
    return await _task_detail(state, task_id)


def _resolution(state: AppState, result: TurnResult) -> dict[str, Any] | None:
    return result.message


@router.post("/agent/review-tasks/{task_id}/resolve")
async def resolve_review(task_id: uuid.UUID, body: ResolveRequest, agent: Agent) -> dict[str, Any]:
    state, user = agent
    try:
        _, result = await resolve(
            state.db,
            state.runner,
            task_id,
            Actor(agent_id=user.id, email=user.email),
            body.action,
            body.notes,
            body.clerical_edits,
        )
    except ReviewError as exc:
        raise _review_error(exc) from exc
    except TurnInProgressError as exc:
        raise HTTPException(409, detail={"code": "conversation_busy", "message": "Busy."}) from exc
    except Exception as exc:
        raise HTTPException(
            502, detail={"code": "resume_failed", "message": "Saved, but resuming failed; retry."}
        ) from exc
    return {
        "task": await _task_detail(state, task_id),
        "customerMessage": _resolution(state, result),
    }


@router.post("/agent/review-tasks/{task_id}/retry-resume")
async def retry_review(task_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, _ = agent
    try:
        _, result = await retry_resume(state.db, state.runner, task_id)
    except ReviewError as exc:
        raise _review_error(exc) from exc
    return {
        "task": await _task_detail(state, task_id),
        "customerMessage": result.message if result else None,
    }


# --------------------------------------------------------------------------- conversations


@router.post("/agent/conversations/{conversation_id}/messages")
async def agent_reply(
    conversation_id: uuid.UUID, body: AgentMessage, agent: Agent
) -> dict[str, Any]:
    """Human reply during a takeover. Stored, and delivered by the conversation's channel."""
    state, user = agent
    try:
        # Under the conversation lock, like every other writer (review 02, finding 9).
        async with conversation_lock(state.db, conversation_id, wait_seconds=3):
            view, channel = await _store_agent_reply(state, user, conversation_id, body.text)
    except ConversationBusyError as exc:
        raise HTTPException(
            409, detail={"code": "conversation_busy", "message": "Busy; retry in a moment."}
        ) from exc
    if state.runner.deliver is not None and channel != "web":
        await state.runner.deliver(conversation_id, view["content"], {})
    return view


async def _store_agent_reply(
    state: AppState, user: AgentUser, conversation_id: uuid.UUID, text: str
) -> tuple[dict[str, Any], str]:
    async with state.db.transaction() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(
                404, detail={"code": "not_found", "message": "No such conversation."}
            )
        if conversation.status != "human_takeover":
            raise HTTPException(
                409, detail={"code": "not_in_takeover", "message": "Only during a handoff."}
            )
        if conversation.channel == "whatsapp":
            last = conversation.last_customer_message_at
            if not last or (datetime.now(UTC) - last).total_seconds() >= 23.9 * 3600:
                raise HTTPException(
                    409,
                    detail={
                        "code": "whatsapp_window_closed",
                        "message": "Use the follow-up template.",
                    },
                )
        customer = await session.get(Customer, conversation.customer_id)
        if customer is not None and customer.erased_at is not None:
            raise HTTPException(
                404, detail={"code": "not_found", "message": "Conversation erased."}
            )
        message = await conversations.store_assistant_message(
            session,
            conversation_id,
            None,
            sanitize(text, source="agent").stored_text,
            {"agent": {"id": str(user.id), "displayName": user.display_name}},
            role="agent",
        )
        audit(
            session,
            actor_type="agent",
            actor_id=str(user.id),
            action="conversation.agent_reply",
            entity_type="conversation",
            entity_id=str(conversation_id),
        )
        return conversations.message_view(message), conversation.channel


def _conversation_summary(c: Conversation, customer: Customer | None, count: int) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "channel": c.channel,
        "status": c.status,
        "customerDisplayName": customer.display_name if customer else None,
        "lastActivityAt": c.last_activity_at.isoformat(),
        "createdAt": c.created_at.isoformat(),
        "messageCount": count,
        "locale": c.locale,
    }


@router.get("/agent/conversations")
async def list_conversations(
    agent: Agent,
    status: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query(pattern="^(web|whatsapp)$")] = None,
) -> dict[str, Any]:
    state, _ = agent
    counts = (
        select(Message.conversation_id, func.count().label("n"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    stmt = (
        select(Conversation, Customer, counts.c.n)
        .join(Customer, Customer.id == Conversation.customer_id)
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .order_by(Conversation.last_activity_at.desc())
        .limit(100)
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    async with state.db.session() as session:
        rows = (await session.execute(stmt)).all()
    return {
        "items": [_conversation_summary(c, cu, n or 0) for c, cu, n in rows],
        "nextCursor": None,
    }


def _run_view(run: AgentRun) -> dict[str, Any]:
    duration = (
        int((run.finished_at - run.started_at).total_seconds() * 1000) if run.finished_at else None
    )
    return {
        "id": str(run.id),
        "route": run.route,
        "status": run.status,
        "startedAt": run.started_at.isoformat(),
        "durationMs": duration,
        "models": run.models,
        "fallbackUsed": run.fallback_used,
        "inputTokens": run.input_tokens,
        "outputTokens": run.output_tokens,
        "costEstimateMicros": run.cost_estimate_micros,
        "guardrailActions": run.guardrail_actions,
    }


@router.get("/agent/conversations/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, _ = agent
    async with state.db.session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(
                404, detail={"code": "not_found", "message": "No such conversation."}
            )
        customer = await session.get(Customer, conversation.customer_id)
        # The whole transcript: agents need the latest messages, not the first page (#25).
        messages = await conversations.list_messages(session, conversation_id, limit=5000)
        runs = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.conversation_id == conversation_id)
                    .order_by(AgentRun.started_at)
                )
            )
            .scalars()
            .all()
        )
        quotes = (
            (await session.execute(select(Quote).where(Quote.conversation_id == conversation_id)))
            .scalars()
            .all()
        )
        product_views = (
            {
                p.id: p
                for p in await list_products(
                    session, product_ids=[q.product_id for q in quotes], active_only=False
                )
            }
            if quotes
            else {}
        )
        applications = (
            await session.execute(
                select(Application, ProductText.name)
                .join(
                    ProductText,
                    (ProductText.product_id == Application.product_id)
                    & (ProductText.locale == "en"),
                )
                .where(Application.conversation_id == conversation_id)
            )
        ).all()
    thread = await state.graph.aget_state({"configurable": {"thread_id": str(conversation_id)}})
    return {
        "conversation": _conversation_summary(conversation, customer, len(messages)),
        "messages": [conversations.message_view(m) for m in messages],
        "profile": (thread.values or {}).get("profile") or {},
        "quotes": [
            quote_artifact(q, product_views[q.product_id], locale=conversation.locale)
            for q in quotes
            if q.product_id in product_views
        ],
        "applications": [
            {
                "id": str(a.id),
                "status": a.status,
                "productName": name,
                "createdAt": a.created_at.isoformat(),
            }
            for a, name in applications
        ],
        "runs": [_run_view(r) for r in runs],
    }


@router.get("/agent/runs/{run_id}")
async def get_run(run_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, _ = agent
    async with state.db.session() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "No such run."})
        steps = (
            (
                await session.execute(
                    select(AgentRunStep)
                    .where(AgentRunStep.run_id == run_id)
                    .order_by(AgentRunStep.seq)
                )
            )
            .scalars()
            .all()
        )
    return _run_view(run) | {
        "steps": [
            {
                "seq": s.seq,
                "kind": s.kind,
                "name": s.name,
                "startedAt": s.started_at.isoformat(),
                "durationMs": s.duration_ms,
                "model": s.model,
                "provider": s.provider,
                "inputTokens": s.input_tokens,
                "outputTokens": s.output_tokens,
                "summary": s.summary,
            }
            for s in steps
        ]
    }


# --------------------------------------------------------------------------- leads & applications


def _lead_view(lead: Lead, customer: Customer | None, assigned: AgentUser | None) -> dict[str, Any]:
    return {
        "id": str(lead.id),
        "kind": lead.kind,
        "status": lead.status,
        "lineInterest": lead.line_interest,
        "source": lead.source,
        "customerDisplayName": customer.display_name if customer else None,
        "assignedAgent": {"id": str(assigned.id), "displayName": assigned.display_name}
        if assigned
        else None,
        "nextFollowUpAt": lead.next_follow_up_at.isoformat() if lead.next_follow_up_at else None,
        "createdAt": lead.created_at.isoformat(),
        # What the agent needs to act on the lead, including one created through MCP (#27).
        "contact": {
            "email": customer.email if customer else None,
            "phone": customer.phone_e164 if customer else None,
        },
        "consent": {
            "at": lead.contact_consent_at.isoformat() if lead.contact_consent_at else None,
            "source": lead.contact_consent_source,
            "textVersion": lead.consent_text_version,
        },
        "conversationId": str(lead.conversation_id) if lead.conversation_id else None,
    }


@router.get("/agent/leads")
async def list_leads(
    agent: Agent,
    status: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    state, _ = agent
    stmt = select(Lead, Customer).join(Customer, Customer.id == Lead.customer_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    if kind:
        stmt = stmt.where(Lead.kind == kind)
    async with state.db.session() as session:
        rows = (await session.execute(stmt.order_by(Lead.created_at.desc()).limit(100))).all()
        items = []
        for lead, customer in rows:
            assigned = (
                await session.get(AgentUser, lead.assigned_agent_id)
                if lead.assigned_agent_id
                else None
            )
            items.append(_lead_view(lead, customer, assigned))
    return {"items": items, "nextCursor": None}


@router.patch("/agent/leads/{lead_id}")
async def patch_lead(lead_id: uuid.UUID, body: LeadUpdate, agent: Agent) -> dict[str, Any]:
    state, user = agent
    async with state.db.transaction() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "No such lead."})
        if body.status:
            lead.status = body.status
        if body.next_follow_up_at:
            lead.next_follow_up_at = body.next_follow_up_at
        audit(
            session,
            actor_type="agent",
            actor_id=str(user.id),
            action="lead.updated",
            entity_type="lead",
            entity_id=str(lead.id),
            details=body.model_dump(exclude_none=True, mode="json"),
        )
        customer = await session.get(Customer, lead.customer_id)
        return _lead_view(lead, customer, None)


@router.get("/agent/applications/{application_id}")
async def get_application(application_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, _ = agent
    async with state.db.session() as session:
        application = await session.get(Application, application_id)
        if application is None:
            raise HTTPException(
                404, detail={"code": "not_found", "message": "No such application."}
            )
        snapshots = (
            (
                await session.execute(
                    select(ApplicationSnapshot)
                    .where(ApplicationSnapshot.application_id == application_id)
                    .order_by(ApplicationSnapshot.version)
                )
            )
            .scalars()
            .all()
        )
    return {
        "id": str(application.id),
        "status": application.status,
        "productId": str(application.product_id),
        "quoteId": str(application.quote_id),
        "currentSnapshotVersion": application.current_snapshot_version,
        "snapshots": [
            {
                "version": s.version,
                "data": s.data,
                "needsProfile": s.needs_profile,
                "createdBy": s.created_by,
                "createdAt": s.created_at.isoformat(),
            }
            for s in snapshots
        ],
    }


# --------------------------------------------------------------- catalog, metrics, outbox


@router.get("/agent/catalog/products")
async def catalog_products(agent: Agent) -> dict[str, Any]:
    state, _ = agent
    async with state.db.session() as session:
        products = await list_products(session, active_only=False)
    return {
        "items": [
            {
                "id": str(p.id),
                "code": p.code,
                "line": p.line,
                "carrier": {"code": p.carrier_code, "name": p.carrier_name},
                "name": p.name,
                "summary": p.summary,
                "attributes": p.attributes,
                "currency": p.currency,
                "billingPeriod": p.billing_period,
                "active": p.active,
            }
            for p in products
        ]
    }


@router.get("/agent/catalog/products/{product_id}/rate-tables")
async def rate_tables(product_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, _ = agent
    async with state.db.session() as session:
        if await session.get(Product, product_id) is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "No such product."})
        tables = (
            (
                await session.execute(
                    select(RateTable)
                    .where(RateTable.product_id == product_id)
                    .order_by(RateTable.effective_from.desc())
                )
            )
            .scalars()
            .all()
        )
    return {
        "items": [
            {
                "id": str(t.id),
                "version": t.version,
                "effectiveFrom": t.effective_from.isoformat(),
                "effectiveTo": t.effective_to.isoformat() if t.effective_to else None,
                "publishedAt": t.published_at.isoformat() if t.published_at else None,
                "factors": t.factors,
            }
            for t in tables
        ]
    }


@router.get("/agent/metrics/summary")
async def metrics(agent: Agent) -> dict[str, Any]:
    state, _ = agent
    sql = text(
        """
        SELECT
          (SELECT count(*) FROM app.review_tasks
            WHERE status IN ('open', 'claimed', 'resolving')) AS open_reviews,
          (SELECT coalesce(avg(extract(epoch FROM resolved_at - created_at)) / 60, 0)
             FROM app.review_tasks WHERE resolved_at IS NOT NULL) AS avg_review_minutes,
          (SELECT count(*) FROM app.leads WHERE created_at >= current_date) AS leads_today,
          (SELECT count(*) FROM app.agent_runs WHERE started_at >= current_date) AS runs_today,
          (SELECT count(*) FROM app.agent_runs
            WHERE started_at >= current_date AND status = 'error') AS errors_today,
          (SELECT count(*) FROM app.agent_runs
            WHERE started_at >= current_date AND fallback_used) AS fallbacks_today,
          (SELECT coalesce(max(calls), 0) FROM app.llm_usage_counters
            WHERE day = current_date) AS model_calls,
          (SELECT count(*) FROM app.outbound_messages WHERE channel = 'whatsapp'
             AND created_at >= date_trunc('month', now())) AS whatsapp_outbound,
          (SELECT count(*) FROM app.outbox_events
            WHERE dead_lettered_at IS NOT NULL) AS dead_letters
        """
    )
    async with state.db.session() as session:
        row = (await session.execute(sql)).mappings().one()
        by_status = dict(
            (
                await session.execute(
                    select(Application.status, func.count()).group_by(Application.status)
                )
            ).all()
        )
        guardrails: dict[str, int] = dict(
            (
                await session.execute(
                    text(
                        "SELECT action, count(*) FROM app.agent_runs, "
                        "unnest(guardrail_actions) AS action "
                        "WHERE started_at >= now() - interval '7 days' GROUP BY action"
                    )
                )
            ).all()
        )
    runs = int(row["runs_today"]) or 1
    return {
        "openReviews": int(row["open_reviews"]),
        "avgReviewMinutes": round(float(row["avg_review_minutes"]), 1),
        "leadsToday": int(row["leads_today"]),
        "applicationsByStatus": {k: int(v) for k, v in by_status.items()},
        "runsToday": int(row["runs_today"]),
        "errorRate": round(int(row["errors_today"]) / runs, 3),
        "fallbackRate": round(int(row["fallbacks_today"]) / runs, 3),
        "guardrailActions": {k: int(v) for k, v in guardrails.items()},
        "modelCallsToday": int(row["model_calls"]),
        "modelCallLimit": state.settings.llm_daily_call_limit,
        "whatsappOutboundThisMonth": int(row["whatsapp_outbound"]),
        "deadLetteredEvents": int(row["dead_letters"]),
    }


@router.get("/agent/outbox")
async def outbox(
    agent: Agent, status: Annotated[str, Query(pattern="^(dead|pending)$")] = "dead"
) -> dict[str, Any]:
    state, _ = agent
    stmt = select(OutboxEvent)
    stmt = (
        stmt.where(OutboxEvent.dead_lettered_at.is_not(None))
        if status == "dead"
        else stmt.where(OutboxEvent.delivered_at.is_(None), OutboxEvent.dead_lettered_at.is_(None))
    )
    async with state.db.session() as session:
        events = (await session.execute(stmt.order_by(OutboxEvent.created_at).limit(100))).scalars()
        return {
            "items": [
                {
                    "id": str(e.id),
                    "eventType": e.event_type,
                    "attempts": e.attempts,
                    "lastError": e.last_error,
                    "createdAt": e.created_at.isoformat(),
                }
                for e in events
            ]
        }


@router.post("/agent/outbox/{event_id}/retry")
async def retry_event(event_id: uuid.UUID, agent: Agent) -> dict[str, Any]:
    state, user = agent
    async with state.db.transaction() as session:
        result = await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.dead_lettered_at.is_not(None))
            .values(dead_lettered_at=None, attempts=0, available_at=datetime.now(UTC))
        )
        if not getattr(result, "rowcount", 0):
            raise HTTPException(
                404, detail={"code": "not_found", "message": "No such dead letter."}
            )
        audit(
            session,
            actor_type="agent",
            actor_id=str(user.id),
            action="outbox.retry",
            entity_type="outbox_event",
            entity_id=str(event_id),
        )
    from policypilot.services.events import nudge_dispatcher

    nudge_dispatcher()
    return {"retried": True}
