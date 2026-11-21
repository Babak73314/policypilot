"""Leads, applications and immutable application snapshots (data-model.md#sales)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import (
    Application,
    ApplicationSnapshot,
    Customer,
    Lead,
    PolicyDocument,
    Quote,
)
from policypilot.services.events import audit, enqueue_event

CONSENT_TEXT_VERSION = "2026-11-contact-v1"
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(\+?\d[\d ().-]{6,}\d)")
NAME = re.compile(
    r"(?i:my name is|i am|i'm|name:|this is)\s+([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,2})"
)


@dataclass(frozen=True)
class ContactDetails:
    name: str | None
    email: str | None
    phone: str | None


def parse_contact(stored_text: str) -> ContactDetails:
    """Deterministic parsing from sanitised text: contact details never pass through a model."""
    email = EMAIL.search(stored_text)
    phone = None
    for match in PHONE.finditer(stored_text):
        digits = re.sub(r"\D", "", match.group(1))
        if 7 <= len(digits) <= 15:
            phone = ("+" if match.group(1).strip().startswith("+") else "") + digits
            break
    name = NAME.search(stored_text)
    return ContactDetails(
        name=name.group(1).strip() if name else None,
        email=email.group(0).lower() if email else None,
        phone=phone,
    )


def mask_contact(email: str | None, phone: str | None) -> str:
    parts = []
    if email:
        user, _, domain = email.partition("@")
        parts.append(f"{user[:1]}***@{domain}")
    if phone:
        parts.append(f"***{phone[-4:]}")
    return ", ".join(parts) or "not provided"


async def update_contact(
    session: AsyncSession, customer_id: uuid.UUID, details: ContactDetails
) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise LookupError("customer not found")
    if details.name:
        customer.display_name = details.name
    if details.email:
        customer.email = details.email
    if details.phone:
        customer.phone_e164 = details.phone
    return customer


async def create_lead(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    kind: str,
    source: str,
    line_interest: list[str],
    profile: dict[str, Any],
    consent_source: str,
    first_name: str | None,
) -> Lead:
    lead = Lead(
        customer_id=customer_id,
        conversation_id=conversation_id,
        kind=kind,
        source=source,
        line_interest=line_interest,
        profile=profile,
        contact_consent_at=datetime.now(UTC),
        contact_consent_source=consent_source,
        consent_text_version=CONSENT_TEXT_VERSION,
        next_follow_up_at=datetime.now(UTC) + timedelta(hours=48),
    )
    session.add(lead)
    await session.flush()
    await enqueue_event(
        session,
        "lead.created",
        {
            "leadId": str(lead.id),
            "source": source,
            "kind": kind,
            "lineInterest": line_interest,
            "customerDisplayName": (first_name or "").split(" ")[0] or None,
            "customerId": str(customer_id),
        },
        key=f"lead:{lead.id}:created",
        links={"dashboard": f"/agent/leads/{lead.id}"},
    )
    audit(
        session,
        actor_type="assistant",
        action="lead.created",
        entity_type="lead",
        entity_id=str(lead.id),
        details={"kind": kind, "source": source},
    )
    return lead


async def start_application(
    session: AsyncSession,
    *,
    lead: Lead,
    conversation_id: uuid.UUID,
    quote: Quote,
) -> Application:
    if quote.conversation_id not in (None, conversation_id) or quote.customer_id not in (
        None,
        lead.customer_id,
    ):
        raise PermissionError("quote does not belong to this conversation")
    application = Application(
        lead_id=lead.id,
        customer_id=lead.customer_id,
        conversation_id=conversation_id,
        product_id=quote.product_id,
        quote_id=quote.id,
        status="draft",
    )
    session.add(application)
    await session.flush()
    return application


async def freeze_snapshot(
    session: AsyncSession,
    application: Application,
    *,
    customer: Customer,
    needs_profile: dict[str, Any],
    created_by: str,
    edits: dict[str, str] | None = None,
) -> ApplicationSnapshot:
    """Create the next immutable snapshot version (the record a review decision refers to)."""
    quote = await session.get(Quote, application.quote_id)
    if quote is None:
        raise LookupError("quote not found")
    document_id = (
        await session.execute(
            select(PolicyDocument.id).where(
                PolicyDocument.product_id == application.product_id,
                PolicyDocument.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    version = (
        await session.execute(
            select(func.coalesce(func.max(ApplicationSnapshot.version), 0)).where(
                ApplicationSnapshot.application_id == application.id
            )
        )
    ).scalar_one() + 1
    data = {"name": customer.display_name, "email": customer.email, "phone": customer.phone_e164}
    data.update({k: v for k, v in (edits or {}).items() if k in ("name", "email", "phone") and v})
    snapshot = ApplicationSnapshot(
        application_id=application.id,
        version=version,
        data=data,
        needs_profile=needs_profile,
        quote_id=quote.id,
        rate_table_id=quote.rate_table_id,
        policy_document_id=document_id,
        created_by=created_by,
    )
    session.add(snapshot)
    application.current_snapshot_version = version
    await session.flush()
    return snapshot
