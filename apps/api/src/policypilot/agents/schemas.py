"""Structured-output and tool-argument schemas.

Every field is required (nullable where optional) and extra keys are forbidden, so the same schema
works with Claude strict tools / `output_config.format` and with OpenAI strict mode (ADR-0006).
Numeric and length constraints are validated in code, not in the schema, because strict modes
support only a subset of JSON Schema keywords.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "greeting",
    "needs_analysis",
    "product_question",
    "compare",
    "quote",
    "apply",
    "contact_request",
    "human",
    "out_of_scope",
]
Line = Literal["life", "health", "auto"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RouteDecision(_Strict):
    """Classify the customer's latest message for an insurance agency assistant."""

    intent: Intent = Field(description="What the customer wants in this message")
    line: Line | None = Field(description="Product line the message is about, if any")
    confidence: float = Field(description="Confidence in the intent, between 0 and 1")


class ProfileDelta(_Strict):
    """Facts the customer stated in the latest message. Use null for anything not stated.

    Never record health conditions, smoking, gender, identifiers or contact details.
    """

    lines: list[Line] | None = Field(description="Product lines the customer is interested in")
    age: int | None = Field(description="Customer age in years")
    household_size: int | None = Field(description="People in the household")
    dependents: int | None = Field(description="Dependants to include (children etc.)")
    monthly_budget: float | None = Field(description="Monthly budget in major currency units")
    region: Literal["north", "central", "south"] | None = Field(description="Region")
    coverage_goal: float | None = Field(description="Life cover amount in major currency units")
    term_years: Literal[10, 20, 30] | None = Field(description="Life cover term in years")
    tier_preference: Literal["bronze", "silver", "gold"] | None = Field(
        description="Health tier preference"
    )
    driver_age: int | None = Field(description="Main driver's age")
    vehicle_class: Literal["economy", "standard", "premium", "suv"] | None = Field(
        description="Type of car"
    )
    annual_mileage_band: Literal["low", "medium", "high"] | None = Field(
        description="low < 8,000 miles a year, medium 8,000-15,000, high > 15,000"
    )
    claims_last_5y: int | None = Field(description="Insurance claims in the last 5 years")
    coverage_level: Literal["liability", "standard", "comprehensive"] | None = Field(
        description="Auto coverage level preference"
    )


# --------------------------------------------------------------------------- advisor tools


class SearchProducts(_Strict):
    """Catalog discovery without prices: products of a line, optionally with required features."""

    line: Line
    must_have: list[str] | None = Field(
        description="Attribute keys that must be present/true, e.g. ['roadside', 'telehealth']"
    )


class QuoteProducts(_Strict):
    """Price every eligible product of a line from the customer's profile, cheapest first.

    Use when the customer asks for prices or what fits a budget. Requires a complete profile.
    """

    line: Line
    max_monthly_budget: float | None = Field(
        description="Monthly budget in major units if the customer gave one"
    )


class GetQuote(_Strict):
    """Price one product from the customer's profile."""

    product_id: str
    coverage_override: float | None = Field(
        description="Life cover amount the customer stated in THIS message, major units; else null"
    )


class CompareProducts(_Strict):
    """Side-by-side attribute comparison of 2-4 products (and their quotes from this turn)."""

    product_ids: list[str] = Field(description="2 to 4 product ids")


class SearchPolicyDocuments(_Strict):
    """Search the current policy wordings. Cite results as [doc:<chunk_id>]."""

    query: str
    product_id: str | None = Field(description="Limit to one product's wording if known")
