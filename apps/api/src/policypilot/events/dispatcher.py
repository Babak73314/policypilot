"""Outbox dispatcher (ADR-0012): event-driven delivery of domain events to n8n.

Woken after commits (`nudge`) and once at start-up. It sweeps only while undelivered events exist,
so an idle system sends no queries (docs/deployment/aws.md). At-least-once, with backoff and a
dead-letter state after 10 attempts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import func, select

from policypilot.config import Settings
from policypilot.db.models import OutboxEvent
from policypilot.db.session import Database

log = structlog.get_logger(__name__)
MAX_ATTEMPTS = 10
SWEEP_SECONDS = 30


class OutboxDispatcher:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=10)
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    @property
    def enabled(self) -> bool:
        return bool(self.settings.n8n_events_url)

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run())
            self.nudge()

    def nudge(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping:
            await self._wake.wait()
            self._wake.clear()
            try:
                remaining = await self.dispatch_due()
            except Exception:  # keep the loop alive; the next nudge or sweep retries
                log.exception("outbox.dispatch_failed")
                remaining = 1
            if remaining:
                # Something is still pending: sweep again later, but only while that is true.
                asyncio.get_running_loop().call_later(SWEEP_SECONDS, self._wake.set)

    async def dispatch_due(self) -> int:
        """Deliver due events; return how many undelivered events remain."""
        while True:
            async with self.db.transaction() as session:
                events = (
                    (
                        await session.execute(
                            select(OutboxEvent)
                            .where(
                                OutboxEvent.delivered_at.is_(None),
                                OutboxEvent.dead_lettered_at.is_(None),
                                OutboxEvent.available_at <= datetime.now(UTC),
                            )
                            .order_by(OutboxEvent.created_at)
                            .limit(20)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                for event in events:
                    await self._deliver(event)
            if not events:
                break
        async with self.db.session() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.delivered_at.is_(None), OutboxEvent.dead_lettered_at.is_(None)
                    )
                )
                or 0
            )

    async def _deliver(self, event: OutboxEvent) -> None:
        event.attempts += 1
        try:
            response = await self.http.post(
                self.settings.n8n_events_url,
                content=json.dumps(event.payload),
                headers={
                    "Content-Type": "application/json",
                    "X-PolicyPilot-Event-Id": str(event.id),
                    "X-PolicyPilot-Event-Type": event.event_type,
                    "X-PolicyPilot-Token": self.settings.n8n_webhook_token.get_secret_value(),
                },
            )
            response.raise_for_status()
            event.delivered_at = datetime.now(UTC)
            event.last_error = None
        except httpx.HTTPError as exc:
            event.last_error = type(exc).__name__
            if event.attempts >= MAX_ATTEMPTS:
                event.dead_lettered_at = datetime.now(UTC)
            else:
                delay = min(600, 2 * 2**event.attempts)
                event.available_at = datetime.now(UTC) + timedelta(seconds=delay)

    async def close(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
        await self.http.aclose()
