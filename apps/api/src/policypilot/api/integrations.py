"""Integration API for n8n (API token with the `integrations` scope; docs/design/api.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from policypilot.api.state import AppState, app_state
from policypilot.db.models import (
    AgentRun,
    AgentUser,
    Application,
    Conversation,
    IntegrationFollowUp,
    Lead,
    OutboxEvent,
    ReviewTask,
)
from policypilot.services.events import audit
from policypilot.services.tokens import TokenPrincipal, verify_token

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


async def integration_client(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> tuple[AppState, TokenPrincipal]:
    state = app_state(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail={"code": "missing_token", "message": "Token required."})
    async with state.db.transaction() as session:
        principal = await verify_token(session, authorization[7:].strip())
    if principal is None or not principal.has("integrations"):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Needs integrations."})
    state.limiter.check(f"integration:{principal.token_id}", 120, 60)
    return state, principal


Client = Annotated[tuple[AppState, TokenPrincipal], Depends(integration_client)]


class FollowUp(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    occurrence_id: str = Field(min_length=3, max_length=128)
    channel: str = Field(default="email", max_length=32)
    note: str | None = Field(default=None, max_length=500)


@router.get("/leads/follow-ups-due")
async def follow_ups_due(
    client: Client, before: Annotated[datetime | None, Query()] = None
) -> dict[str, Any]:
    state, _ = client
    cutoff = before or datetime.now(UTC)
    async with state.db.session() as session:
        rows = (
            await session.execute(
                select(Lead, AgentUser.email)
                .outerjoin(AgentUser, AgentUser.id == Lead.assigned_agent_id)
                .where(Lead.status.in_(("new", "contacted")), Lead.next_follow_up_at <= cutoff)
                .order_by(Lead.next_follow_up_at)
                .limit(100)
            )
        ).all()
    return {
        "items": [
            {
                "leadId": str(lead.id),
                "assignedAgentEmail": email,
                "lineInterest": lead.line_interest,
                "createdAt": lead.created_at.isoformat(),
            }
            for lead, email in rows
        ]
    }


@router.post("/leads/{lead_id}/follow-ups")
async def record_follow_up(lead_id: uuid.UUID, body: FollowUp, client: Client) -> dict[str, Any]:
    """Idempotent on `occurrenceId`: a replayed n8n execution changes nothing."""
    state, principal = client
    async with state.db.transaction() as session:
        lead = await session.get(Lead, lead_id, with_for_update=True)
        if lead is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "No such lead."})
        inserted = (
            await session.execute(
                insert(IntegrationFollowUp)
                .values(occurrence_id=body.occurrence_id, lead_id=lead_id, channel=body.channel)
                .on_conflict_do_nothing(index_elements=["occurrence_id"])
                .returning(IntegrationFollowUp.occurrence_id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            return {"duplicate": True}
        lead.status = "contacted" if lead.status == "new" else lead.status
        lead.next_follow_up_at = datetime.now(UTC) + timedelta(hours=48)
        audit(
            session,
            actor_type="integration",
            actor_id=principal.name,
            action="lead.follow_up",
            entity_type="lead",
            entity_id=str(lead_id),
            details={
                "channel": body.channel,
                # The audit log is append-only and outlives erasure: no free text in it.
                "has_note": bool(body.note and body.note.strip()),
            },
        )
    return {"duplicate": False, "nextFollowUpAt": lead.next_follow_up_at.isoformat()}


@router.get("/digest")
async def digest(
    client: Client, day: Annotated[date | None, Query(alias="date")] = None
) -> dict[str, Any]:
    state, _ = client
    start = datetime.combine(
        day or (datetime.now(UTC).date() - timedelta(days=1)), datetime.min.time(), UTC
    )
    end = start + timedelta(days=1)
    async with state.db.session() as session:
        conversations = await session.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.created_at >= start, Conversation.created_at < end)
        )
        leads = await session.scalar(
            select(func.count())
            .select_from(Lead)
            .where(Lead.created_at >= start, Lead.created_at < end)
        )
        applications = dict(
            (
                await session.execute(
                    select(Application.status, func.count())
                    .where(Application.created_at >= start, Application.created_at < end)
                    .group_by(Application.status)
                )
            ).all()
        )
        open_reviews = await session.scalar(
            select(func.count())
            .select_from(ReviewTask)
            .where(ReviewTask.status.in_(("open", "claimed", "resolving")))
        )
        fallbacks = await session.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.started_at >= start, AgentRun.started_at < end, AgentRun.fallback_used)
        )
        dead = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.dead_lettered_at.is_not(None))
        )
    return {
        "date": start.date().isoformat(),
        "conversations": int(conversations or 0),
        "leads": int(leads or 0),
        "applicationsByStatus": {k: int(v) for k, v in applications.items()},
        "openReviews": int(open_reviews or 0),
        "fallbackRuns": int(fallbacks or 0),
        "deadLetteredEvents": int(dead or 0),
    }
