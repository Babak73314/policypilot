"""Review tasks: claim, resolve (compare-and-set, then graph resume), retry and expiry."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.agents.runner import TurnResult, TurnRunner
from policypilot.db.models import Application, ApplicationSnapshot, Quote, ReviewTask
from policypilot.db.session import Database
from policypilot.security.ingress import sanitize
from policypilot.services.events import audit

ACTIONS = {
    "application_review": {"approve", "request_changes", "reject", "edit_and_approve"},
    "handoff": {"return_to_assistant", "close"},
}
CLERICAL_FIELDS = frozenset({"name", "email", "phone"})
REVIEW_TTL = timedelta(days=14)


class ReviewError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Actor:
    agent_id: uuid.UUID
    email: str


def graph_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """What the graph (and so its checkpoints) may hold of a decision (review 02, finding 3).

    Clerical values and the reviewer's email stay on the review task row; finalize_review reads
    them from there. The graph gets the action, the masked note and which fields were edited.
    """
    return {
        "action": decision.get("action"),
        "notes": decision.get("notes") or "",
        "agent_id": decision.get("agent_id"),
        "clerical_fields": sorted((decision.get("clerical_edits") or {}).keys()),
    }


async def claim(db: Database, task_id: uuid.UUID, actor: Actor) -> None:
    async with db.transaction() as session:
        task = await session.get(ReviewTask, task_id, with_for_update=True)
        if task is None:
            raise ReviewError(404, "not_found", "Review task not found.")
        if task.status == "claimed" and task.claimed_by == actor.agent_id:
            return
        if task.status != "open":
            raise ReviewError(409, "review_not_claimable", "Already claimed or resolved.")
        task.status = "claimed"
        task.claimed_by = actor.agent_id
        task.claimed_at = datetime.now(UTC)
        audit(
            session,
            actor_type="agent",
            actor_id=str(actor.agent_id),
            action="review.claimed",
            entity_type="review_task",
            entity_id=str(task.id),
        )


async def _check_application(
    session: AsyncSession, task: ReviewTask, action: str, edits: dict[str, Any] | None
) -> None:
    if edits and action != "edit_and_approve":
        raise ReviewError(422, "edits_need_edit_and_approve", "Use edit_and_approve for edits.")
    if edits and set(edits) - CLERICAL_FIELDS:
        raise ReviewError(
            422,
            "requires_customer_confirmation",
            "Only name, email and phone can be edited; request changes for anything else.",
        )
    if action in ("approve", "edit_and_approve") and task.application_id is not None:
        application = await session.get(Application, task.application_id)
        assert application is not None
        snapshot = await session.get(
            ApplicationSnapshot, (application.id, application.current_snapshot_version)
        )
        quote = await session.get(Quote, snapshot.quote_id if snapshot else application.quote_id)
        if quote is not None and quote.expires_at <= datetime.now(UTC):
            raise ReviewError(422, "quote_expired", "The quote expired; request changes instead.")


async def resolve(
    db: Database,
    runner: TurnRunner,
    task_id: uuid.UUID,
    actor: Actor | None,
    action: str,
    notes: str | None = None,
    clerical_edits: dict[str, Any] | None = None,
) -> tuple[ReviewTask, TurnResult]:
    """Record the decision (claimed/open -> resolving), then resume the graph with it."""
    async with db.transaction() as session:
        task = await session.get(ReviewTask, task_id, with_for_update=True)
        if task is None:
            raise ReviewError(404, "not_found", "Review task not found.")
        if action not in ACTIONS[task.kind] | ({"expire"} if actor is None else set()):
            raise ReviewError(422, "invalid_action", f"{action} is not valid for {task.kind}.")
        if task.status not in ("open", "claimed"):
            raise ReviewError(
                409, "review_not_resolvable", "This review is already being resolved."
            )
        if task.kind == "application_review":
            await _check_application(session, task, action, clerical_edits)
        # The note goes to the customer and into model history: contact details are masked.
        clean_notes = sanitize(notes or "", source="review_note").model_text[:1000]
        task.status = "resolving"
        task.decision = {
            "action": action,
            "notes": clean_notes,
            "clerical_edits": clerical_edits or {},
            "agent_id": str(actor.agent_id) if actor else None,
            "agent_email": actor.email if actor else None,
        }
        audit(
            session,
            actor_type="agent" if actor else "system",
            actor_id=str(actor.agent_id) if actor else None,
            action=f"review.decision.{action}",
            entity_type="review_task",
            entity_id=str(task.id),
        )
        conversation_id = task.conversation_id
        decision = graph_decision(task.decision)
    decision["intervening"] = await runner.intervening(conversation_id, task)
    result = await runner.resume(conversation_id, decision)
    async with db.session() as session:
        refreshed = await session.get(ReviewTask, task_id)
    assert refreshed is not None
    return refreshed, result


async def retry_resume(
    db: Database, runner: TurnRunner, task_id: uuid.UUID
) -> tuple[ReviewTask, TurnResult | None]:
    """Recover a task stuck in `resolving` (agent-design.md#resolving)."""
    async with db.session() as session:
        task = await session.get(ReviewTask, task_id)
    if task is None:
        raise ReviewError(404, "not_found", "Review task not found.")
    if task.status != "resolving" or not task.decision:
        raise ReviewError(409, "not_resolving", "Only a task stuck in resolving can be retried.")
    if not await runner.is_interrupted(task.conversation_id):
        raise ReviewError(409, "already_resumed", "The conversation has already moved on.")
    decision = graph_decision(task.decision)
    decision["intervening"] = await runner.intervening(task.conversation_id, task)
    result = await runner.resume(task.conversation_id, decision)
    async with db.session() as session:
        refreshed = await session.get(ReviewTask, task_id)
    assert refreshed is not None
    return refreshed, result


async def expire_stale(db: Database, runner: TurnRunner) -> int:
    """Expire unresolved reviews older than 14 days (maintenance job)."""
    cutoff = datetime.now(UTC) - REVIEW_TTL
    async with db.session() as session:
        ids = list(
            (
                await session.execute(
                    select(ReviewTask.id).where(
                        ReviewTask.status.in_(("open", "claimed")), ReviewTask.created_at < cutoff
                    )
                )
            ).scalars()
        )
    expired = 0
    for task_id in ids:
        try:
            await resolve(db, runner, task_id, None, "expire")
            expired += 1
        except ReviewError:
            continue
    return expired
