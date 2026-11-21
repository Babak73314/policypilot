"""Retention jobs and seeding behaviour that only show up on real rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from policypilot.db.models import (
    ApiToken,
    Application,
    Conversation,
    Customer,
    Lead,
    ReviewTask,
)

pytestmark = pytest.mark.db


async def _application(session: Any, *, review_status: str) -> tuple[uuid.UUID, uuid.UUID]:
    from policypilot.domain.rating.models import LifeQuoteInputs
    from policypilot.services.catalog import list_products
    from policypilot.services.quoting import quote_product

    customer = Customer(display_name="Old")
    session.add(customer)
    await session.flush()
    conversation = Conversation(customer_id=customer.id, channel="web", locale="en")
    session.add(conversation)
    lead = Lead(customer_id=customer.id, kind="application", source="web")
    session.add(lead)
    await session.flush()
    product = next(p for p in await list_products(session, line="life") if "20" in p.name)
    outcome = await quote_product(
        session,
        product,
        LifeQuoteInputs(age=40, coverage_minor=50_000_000),
        source="assistant",
    )
    assert outcome.quote is not None
    application = Application(
        lead_id=lead.id,
        customer_id=customer.id,
        product_id=product.id,
        quote_id=outcome.quote.id,
        status="approved" if review_status == "resolved" else "pending_review",
    )
    session.add(application)
    await session.flush()
    task = ReviewTask(
        review_key=f"test:{application.id}",
        kind="application_review",
        conversation_id=conversation.id,
        application_id=application.id,
        status=review_status,
    )
    session.add(task)
    await session.flush()
    old = datetime.now(UTC) - timedelta(days=800)
    await session.execute(update(Lead).where(Lead.id == lead.id).values(updated_at=old))
    await session.execute(
        update(Application).where(Application.id == application.id).values(updated_at=old)
    )
    return application.id, task.id


async def test_sales_retention_purges_reviewed_applications(
    prepared_db: Any, settings: Any
) -> None:
    from policypilot.services.maintenance import purge_sales

    async with prepared_db.transaction() as session:
        resolved_app, resolved_task = await _application(session, review_status="resolved")
        open_app, open_task = await _application(session, review_status="open")

    await purge_sales(prepared_db, settings)  # failed with a foreign-key error before the fix

    async with prepared_db.session() as session:
        assert await session.get(Application, resolved_app) is None
        assert await session.get(ReviewTask, resolved_task) is None
        assert await session.get(Application, open_app) is not None  # still under review
        assert await session.get(ReviewTask, open_task) is not None


async def test_reseeding_keeps_a_revoked_token_revoked(prepared_db: Any, settings: Any) -> None:
    from policypilot.rag.embeddings import HashingEmbedder
    from policypilot.seed.run import run_seed

    async with prepared_db.transaction() as session:
        await session.execute(
            update(ApiToken)
            .where(ApiToken.name == "local-mcp")
            .values(revoked_at=datetime.now(UTC))
        )
    await run_seed(prepared_db, settings, HashingEmbedder(settings.embedding_dimensions))
    async with prepared_db.session() as session:
        token = (
            await session.execute(select(ApiToken).where(ApiToken.name == "local-mcp"))
        ).scalar_one()
    assert token.revoked_at is not None
    async with prepared_db.transaction() as session:  # leave the shared test database usable
        await session.execute(
            update(ApiToken).where(ApiToken.name == "local-mcp").values(revoked_at=None)
        )
