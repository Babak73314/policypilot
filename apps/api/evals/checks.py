"""Deterministic checks. They never use a model: prices, citations and redaction are verified
against the database and the rating engine, independently of what the graph claims."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evals.scenarios import Expect
from policypilot.db.models import DocumentChunk, PolicyDocument, Product, Quote, RateTable
from policypilot.domain.rating.engine import rate
from policypilot.domain.rating.models import (
    AutoQuoteInputs,
    HealthQuoteInputs,
    Ineligible,
    LifeQuoteInputs,
)
from policypilot.services.catalog import parse_factors

_INPUTS: TypeAdapter[LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs] = TypeAdapter(
    LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs
)
_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)\s*(k|m)?\b", re.IGNORECASE)


@dataclass
class TurnRecord:
    """What one customer turn produced, as observed through the API and the database."""

    text: str
    reply: str = ""
    status: str | None = None
    error: dict[str, Any] | None = None
    quote_ids: list[str] = field(default_factory=list)
    comparison: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    route: str | None = None
    tools: list[str] = field(default_factory=list)
    guardrail_actions: list[str] = field(default_factory=list)
    guardrail_problems: list[str] = field(default_factory=list)  # why a draft was rejected
    models: list[str] = field(default_factory=list)
    fallback_used: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    latency_ms: float = 0.0


def money_amounts(text: str) -> set[int]:
    """Dollar amounts in `text`, in minor units ("$500k" and "$80.00" both work)."""
    amounts: set[int] = set()
    for raw, suffix in _MONEY.findall(text):
        value = float(raw.replace(",", ""))
        if suffix.lower() == "k":
            value *= 1_000
        elif suffix.lower() == "m":
            value *= 1_000_000
        amounts.add(round(value * 100))
    return amounts


async def verify_quotes(session: AsyncSession, quote_ids: list[str]) -> list[str]:
    """Every quote's premium and variants must equal a fresh run of the rating engine."""
    problems: list[str] = []
    for quote_id in quote_ids:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        if quote is None:
            problems.append(f"quote {quote_id} is not stored")
            continue
        table = await session.get(RateTable, quote.rate_table_id)
        assert table is not None
        outcome = rate(
            _INPUTS.validate_python(quote.inputs), parse_factors(table), currency=quote.currency
        )
        if isinstance(outcome, Ineligible):
            problems.append(f"quote {quote_id} exists but the engine finds the inputs ineligible")
            continue
        if outcome.premium_minor != quote.premium_minor:
            problems.append(
                f"quote {quote_id}: stored premium {quote.premium_minor}"
                f" != engine {outcome.premium_minor}"
            )
        engine_variants = {v.key: v.premium_minor for v in outcome.variants}
        stored_variants = {v["key"]: v["premium_minor"] for v in quote.variants.get("items", [])}
        if engine_variants != stored_variants:
            problems.append(f"quote {quote_id}: variants differ from the engine")
    return problems


async def allowed_amounts(
    session: AsyncSession, conversation_quotes: list[str], customer_text: str
) -> set[int]:
    """Amounts a reply may contain: the customer's own, the catalog's, and stored quotes'."""
    allowed = money_amounts(customer_text)
    for quote_id in conversation_quotes:
        quote = await session.get(Quote, uuid.UUID(quote_id))
        if quote is None:
            continue
        allowed.update({quote.premium_minor, quote.monthly_equivalent_minor})
        allowed.update(v["premium_minor"] for v in quote.variants.get("items", []))
        allowed.update(
            line["amount_minor"]
            for line in quote.breakdown.get("lines", [])
            if line.get("amount_minor")
        )
    for attributes in (await session.execute(select(Product.attributes))).scalars():
        for value in attributes.values():
            if isinstance(value, int):
                allowed.add(value)  # attributes ending in _minor
    return allowed


async def citation_products(session: AsyncSession, chunk_ids: list[int]) -> set[str]:
    if not chunk_ids:
        return set()
    rows = await session.execute(
        select(Product.code)
        .join(PolicyDocument, PolicyDocument.product_id == Product.id)
        .join(DocumentChunk, DocumentChunk.document_id == PolicyDocument.id)
        .where(DocumentChunk.id.in_(chunk_ids))
    )
    return set(rows.scalars())


async def quote_product_codes(session: AsyncSession, quote_ids: list[str]) -> set[str]:
    if not quote_ids:
        return set()
    rows = await session.execute(
        select(Product.code)
        .join(Quote, Quote.product_id == Product.id)
        .where(Quote.id.in_([uuid.UUID(q) for q in quote_ids]))
    )
    return set(rows.scalars())


def check_text(expect: Expect, turn: TurnRecord) -> list[str]:
    problems: list[str] = []
    reply = turn.reply.lower()
    for needle in expect.reply_must_include:
        if needle.lower() not in reply:
            problems.append(f"reply lacks {needle!r}")
    for needle in expect.reply_must_not_include:
        if needle.lower() in reply:
            problems.append(f"reply contains {needle!r}")
    if expect.reply_any_of and not any(n.lower() in reply for n in expect.reply_any_of):
        problems.append(f"reply contains none of {expect.reply_any_of}")
    if expect.questions_max is not None and turn.reply.count("?") > expect.questions_max:
        problems.append(
            f"reply asks {turn.reply.count('?')} questions (max {expect.questions_max})"
        )
    return problems


def check_run(expect: Expect, turn: TurnRecord) -> list[str]:
    problems: list[str] = []
    if expect.route is not None and turn.route not in expect.route:
        problems.append(f"route {turn.route!r} not in {expect.route}")
    for tool in expect.tools_called:
        if tool not in turn.tools:
            problems.append(f"tool {tool} not called (called: {turn.tools})")
    for tool in expect.tools_not_called:
        if tool in turn.tools:
            problems.append(f"tool {tool} called")
    if expect.quotes_min is not None and len(turn.quote_ids) < expect.quotes_min:
        problems.append(f"{len(turn.quote_ids)} quotes (min {expect.quotes_min})")
    if expect.quotes_max is not None and len(turn.quote_ids) > expect.quotes_max:
        problems.append(f"{len(turn.quote_ids)} quotes (max {expect.quotes_max})")
    if expect.comparison is not None and turn.comparison != expect.comparison:
        problems.append(f"comparison artifact {'missing' if expect.comparison else 'present'}")
    if expect.citations_min is not None and len(turn.citations) < expect.citations_min:
        problems.append(f"{len(turn.citations)} citations (min {expect.citations_min})")
    if expect.citations_max is not None and len(turn.citations) > expect.citations_max:
        problems.append(f"{len(turn.citations)} citations (max {expect.citations_max})")
    for action in expect.guardrail_actions_include:
        if action not in turn.guardrail_actions:
            problems.append(f"guardrail action {action} missing ({turn.guardrail_actions})")
    for action in expect.guardrail_actions_exclude:
        if action in turn.guardrail_actions:
            problems.append(f"guardrail action {action} present")
    if expect.status is not None and turn.status != expect.status:
        problems.append(f"conversation status {turn.status!r} (expected {expect.status!r})")
    return problems


def check_profile(expected: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    problems = []
    for key, value in expected.items():
        actual = profile.get(key)
        if isinstance(value, list) and isinstance(actual, list):
            if sorted(map(str, value)) != sorted(map(str, actual)):
                problems.append(f"profile.{key} = {actual!r} (expected {value!r})")
        elif actual != value:
            problems.append(f"profile.{key} = {actual!r} (expected {value!r})")
    return problems
