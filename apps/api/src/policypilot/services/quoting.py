"""Quoting service: the only path from inputs to a stored premium (ADR-0003)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import Quote
from policypilot.domain.rating.engine import rate
from policypilot.domain.rating.models import (
    AutoQuoteInputs,
    HealthQuoteInputs,
    Ineligible,
    LifeQuoteInputs,
    QuoteResult,
)
from policypilot.i18n.formatting import render_premium
from policypilot.services.catalog import (
    ProductView,
    current_rate_table,
    list_products,
    parse_factors,
)

QUOTE_TTL = timedelta(days=30)
Source = Literal["assistant", "mcp", "agent"]
DISCLAIMER = (
    "Estimate only, not an offer. The final premium depends on the carrier's underwriting, and "
    "a licensed agent reviews every application."
)


@dataclass(frozen=True)
class QuoteOutcome:
    product: ProductView
    quote: Quote | None
    ineligible_reasons: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.quote is not None


def inputs_hash(inputs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _jsonable(result: QuoteResult) -> tuple[dict[str, Any], dict[str, Any]]:
    breakdown = {
        "lines": [
            {
                "label": line.label,
                "amount_minor": line.amount_minor,
                "factor": str(line.factor) if line.factor is not None else None,
            }
            for line in result.breakdown
        ]
    }
    variants = {
        "items": [
            {"key": v.key, "label": v.label, "premium_minor": v.premium_minor}
            for v in result.variants
        ]
    }
    return breakdown, variants


async def quote_product(
    session: AsyncSession,
    product: ProductView,
    inputs: LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs,
    *,
    source: Source,
    conversation_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    idempotency_scope: str | None = None,
) -> QuoteOutcome:
    """Rate and persist one quote. Idempotent per (scope, product, inputs) when a scope is given."""
    if inputs.line != product.line:
        return QuoteOutcome(product=product, quote=None, ineligible_reasons=("line_mismatch",))
    table_row = await current_rate_table(session, product.id)
    if table_row is None:
        return QuoteOutcome(product=product, quote=None, ineligible_reasons=("no_rate_table",))
    outcome = rate(inputs, parse_factors(table_row), currency=product.currency)
    if isinstance(outcome, Ineligible):
        return QuoteOutcome(product=product, quote=None, ineligible_reasons=outcome.reasons)

    payload = inputs.model_dump(mode="json")
    digest = inputs_hash(payload)
    key = (
        hashlib.sha256(f"{idempotency_scope}:{product.id}:{digest}".encode()).hexdigest()
        if idempotency_scope
        else None
    )
    breakdown, variants = _jsonable(outcome)
    now = datetime.now(UTC)
    values = {
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "product_id": product.id,
        "rate_table_id": table_row.id,
        "inputs": payload,
        "inputs_hash": digest,
        "premium_minor": outcome.premium_minor,
        "monthly_equivalent_minor": outcome.monthly_equivalent_minor,
        "currency": outcome.currency,
        "billing_period": outcome.billing_period,
        "breakdown": breakdown,
        "variants": variants,
        "engine_version": outcome.engine_version,
        "source": source,
        "idempotency_key": key,
        "expires_at": now + QUOTE_TTL,
    }
    if key is None:
        quote = Quote(**values)
        session.add(quote)
        await session.flush()
        return QuoteOutcome(product=product, quote=quote)
    stmt = insert(Quote).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"])
    await session.execute(stmt)
    quote = (await session.execute(select(Quote).where(Quote.idempotency_key == key))).scalar_one()
    return QuoteOutcome(product=product, quote=quote)


async def quote_products(
    session: AsyncSession,
    *,
    line: str,
    inputs: LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs,
    source: Source,
    locale: str = "en",
    term_years: int | None = None,
    max_monthly_budget_minor: int | None = None,
    product_ids: list[uuid.UUID] | None = None,
    conversation_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    idempotency_scope: str | None = None,
) -> tuple[list[QuoteOutcome], list[QuoteOutcome], bool]:
    """Rate every eligible product of a line. Returns (within_budget, ineligible, over_budget).

    When nothing fits the budget, the cheapest eligible quote is returned with over_budget=True.
    """
    products = await list_products(session, line=line, locale=locale, product_ids=product_ids)
    if line == "life" and term_years is not None:
        products = [p for p in products if p.attributes.get("term_years") == term_years]
    eligible: list[QuoteOutcome] = []
    ineligible: list[QuoteOutcome] = []
    for product in products:
        outcome = await quote_product(
            session,
            product,
            inputs,
            source=source,
            conversation_id=conversation_id,
            customer_id=customer_id,
            idempotency_scope=idempotency_scope,
        )
        (eligible if outcome.eligible else ineligible).append(outcome)
    eligible.sort(key=lambda o: o.quote.monthly_equivalent_minor if o.quote else 0)
    if max_monthly_budget_minor is None:
        return eligible, ineligible, False
    within = [
        o
        for o in eligible
        if o.quote is not None and o.quote.monthly_equivalent_minor <= max_monthly_budget_minor
    ]
    if within or not eligible:
        return within, ineligible, False
    return eligible[:1], ineligible, True


def quote_artifact(
    quote: Quote, product: ProductView, *, locale: str, over_budget: bool = False
) -> dict[str, Any]:
    """The API/SSE representation of a quote card (docs/design/api.md)."""

    def money(minor: int | None) -> dict[str, Any] | None:
        if minor is None:
            return None
        return {"amountMinor": minor, "currency": quote.currency}

    return {
        "type": "quote",
        "quoteId": str(quote.id),
        "productId": str(product.id),
        "productName": product.name,
        "carrierName": product.carrier_name,
        "premium": money(quote.premium_minor),
        "billingPeriod": quote.billing_period,
        "monthlyEquivalent": money(quote.monthly_equivalent_minor),
        "variants": [
            {"key": v["key"], "label": v["label"], "premium": money(v["premium_minor"])}
            for v in quote.variants.get("items", [])
        ],
        "breakdown": [
            {"label": line["label"], "amount": money(line["amount_minor"])}
            for line in quote.breakdown.get("lines", [])
        ],
        "disclaimer": DISCLAIMER,
        "expiresAt": quote.expires_at.isoformat(),
        "overBudget": over_budget,
        "rendered": render_premium(
            quote.premium_minor,
            quote.monthly_equivalent_minor,
            quote.currency,
            quote.billing_period,
            locale,
        ),
    }


def quote_tool_view(quote: Quote, product: ProductView, *, locale: str) -> dict[str, Any]:
    """What the model sees: IDs and the rendered price text it must NOT retype (placeholders)."""
    return {
        "quote_id": str(quote.id),
        "placeholder": f"{{{{quote:{quote.id}}}}}",
        "product_id": str(product.id),
        "product": product.name,
        "carrier": product.carrier_name,
        "billing_period": quote.billing_period,
        "rendered_price_preview": render_premium(
            quote.premium_minor,
            quote.monthly_equivalent_minor,
            quote.currency,
            quote.billing_period,
            locale,
        ),
        "variants": [v["label"] for v in quote.variants.get("items", [])],
    }


async def get_quote(session: AsyncSession, quote_id: uuid.UUID) -> Quote | None:
    return await session.get(Quote, quote_id)
