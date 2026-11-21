"""Retention and recovery jobs (data-model.md#retention-and-erasure).

Each job is idempotent and safe to run on a schedule (an n8n cron or a container task).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from policypilot.config import Settings
from policypilot.db.models import (
    AgentRun,
    Application,
    Conversation,
    InboundEvent,
    Lead,
    Message,
    OutboundMessage,
    OutboxEvent,
    ReviewTask,
    SimulatorOutbox,
    Turn,
)
from policypilot.db.session import Database

INACTIVE_AFTER = timedelta(days=30)
CONVERSATION_RETENTION = timedelta(days=180)
SALES_RETENTION = timedelta(days=730)
RUN_RETENTION = timedelta(days=30)
OUTBOX_RETENTION = timedelta(days=30)

Job = Callable[[Database, Settings], Awaitable[int]]


async def close_inactive(db: Database, settings: Settings) -> int:
    """Close inactive conversations, each under its lock; a busy one waits for the next run."""
    from policypilot.services.locks import ConversationBusyError, conversation_lock

    cutoff = datetime.now(UTC) - INACTIVE_AFTER
    async with db.session() as session:
        ids = list(
            (
                await session.execute(
                    select(Conversation.id).where(
                        Conversation.status == "active", Conversation.last_activity_at < cutoff
                    )
                )
            ).scalars()
        )
    closed = 0
    for conversation_id in ids:
        try:
            async with conversation_lock(db, conversation_id), db.transaction() as session:
                result = await session.execute(
                    update(Conversation)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.status == "active",
                        Conversation.last_activity_at < cutoff,
                    )
                    .values(status="closed", close_reason="inactive", closed_at=datetime.now(UTC))
                )
                closed += int(getattr(result, "rowcount", 0) or 0)
        except ConversationBusyError:
            continue  # a turn is running: it is not inactive
    return closed


async def purge_conversations(db: Database, settings: Settings) -> int:
    from policypilot.agents.checkpoint import open_checkpointer

    cutoff = datetime.now(UTC) - CONVERSATION_RETENTION
    async with db.session() as session:
        ids = list(
            (
                await session.execute(
                    select(Conversation.id).where(
                        Conversation.status == "closed", Conversation.closed_at < cutoff
                    )
                )
            ).scalars()
        )
    if not ids:
        return 0
    async with db.transaction() as session:
        for model in (Message, Turn, InboundEvent, OutboundMessage, SimulatorOutbox):
            await session.execute(delete(model).where(model.conversation_id.in_(ids)))
    async with open_checkpointer(settings) as (saver, _pool):
        for conversation_id in ids:
            await saver.adelete_thread(str(conversation_id))
    return len(ids)


async def purge_sales(db: Database, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - SALES_RETENTION
    async with db.transaction() as session:
        stale_leads = select(Lead.id).where(Lead.updated_at < cutoff, Lead.status != "won")
        # An application with an unresolved review is never purged; resolved reviews of a purged
        # application go with it (review_tasks.application_id has no ON DELETE action).
        under_review = select(ReviewTask.application_id).where(
            ReviewTask.application_id.is_not(None), ReviewTask.status != "resolved"
        )
        stale_applications = select(Application.id).where(
            Application.lead_id.in_(stale_leads),
            Application.updated_at < cutoff,
            Application.id.not_in(under_review),
        )
        await session.execute(
            delete(ReviewTask).where(ReviewTask.application_id.in_(stale_applications))
        )
        await session.execute(delete(Application).where(Application.id.in_(stale_applications)))
        result = await session.execute(
            delete(Lead).where(
                Lead.updated_at < cutoff,
                Lead.status != "won",
                ~Lead.id.in_(select(Application.lead_id)),
            )
        )
    return int(getattr(result, "rowcount", 0) or 0)


async def purge_runs(db: Database, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - RUN_RETENTION
    async with db.transaction() as session:
        result = await session.execute(delete(AgentRun).where(AgentRun.started_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


async def purge_outbox(db: Database, settings: Settings) -> int:
    cutoff = datetime.now(UTC) - OUTBOX_RETENTION
    async with db.transaction() as session:
        result = await session.execute(delete(OutboxEvent).where(OutboxEvent.delivered_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


async def _with_runner(
    db: Database, settings: Settings, job: Callable[[Any], Awaitable[int]]
) -> int:
    """Review jobs resume LangGraph threads, so they need the graph and a runner."""
    from policypilot.agents.checkpoint import open_checkpointer
    from policypilot.agents.graph import build_graph
    from policypilot.agents.models import ModelRoles
    from policypilot.agents.runner import TurnRunner
    from policypilot.channels.whatsapp.service import WhatsAppChannel
    from policypilot.rag.embeddings import HashingEmbedder
    from policypilot.services.templates import TemplateStore

    templates = TemplateStore()
    async with db.session() as session:
        await templates.load(session)
    async with open_checkpointer(settings) as (saver, _pool):
        runner = TurnRunner(
            db=db,
            settings=settings,
            graph=build_graph(saver),
            models=ModelRoles(settings),
            embedder=HashingEmbedder(settings.embedding_dimensions),  # not used by review nodes
            templates=templates,
        )
        # A resumed WhatsApp review must reach the customer too (review 02, finding 7).
        channel = WhatsAppChannel(db=db, settings=settings, runner=runner, templates=templates)
        runner.deliver = channel.deliver
        try:
            result = await job(runner)
            await channel.send_pending()
            return result
        finally:
            await channel.close()


async def expire_reviews(db: Database, settings: Settings) -> int:
    from policypilot.services.reviews import expire_stale

    return await _with_runner(db, settings, lambda runner: expire_stale(db, runner))


async def reconcile_reviews(db: Database, settings: Settings) -> int:
    """Retry resumes of tasks stuck in `resolving` for more than two minutes."""
    from policypilot.db.models import ReviewTask
    from policypilot.services.reviews import ReviewError, retry_resume

    cutoff = datetime.now(UTC) - timedelta(minutes=2)
    async with db.session() as session:
        ids = list(
            (
                await session.execute(
                    select(ReviewTask.id).where(
                        ReviewTask.status == "resolving", ReviewTask.created_at < cutoff
                    )
                )
            ).scalars()
        )

    async def run(runner: Any) -> int:
        fixed = 0
        for task_id in ids:
            try:
                await retry_resume(db, runner, task_id)
                fixed += 1
            except ReviewError:
                continue
        return fixed

    return await _with_runner(db, settings, run) if ids else 0


JOBS: dict[str, Job] = {
    "expire-reviews": expire_reviews,
    "reconcile-reviews": reconcile_reviews,
    "close-inactive": close_inactive,
    "purge-conversations": purge_conversations,
    "purge-sales": purge_sales,
    "purge-runs": purge_runs,
    "purge-outbox": purge_outbox,
}


async def run_jobs(settings: Settings, job: str) -> dict[str, int]:
    names = list(JOBS) if job == "all" else [job]
    unknown = [name for name in names if name not in JOBS]
    if unknown:
        raise ValueError(f"unknown job(s) {unknown}; available: {sorted(JOBS)}")
    db = Database(settings)
    try:
        return {name: await JOBS[name](db, settings) for name in names}
    finally:
        await db.dispose()
