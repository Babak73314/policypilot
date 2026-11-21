"""Deterministic rating engine (ADR-0003).

All arithmetic is Decimal. Each rater records its steps as breakdown lines and rounds
once, half-up, to the currency's minor unit. The model never computes a price; it can only
call this engine through the `get_quote` tool or the MCP tool of the same name.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from policypilot.domain.rating.models import (
    AgeBand,
    AutoQuoteInputs,
    AutoRateTable,
    BillingPeriod,
    BreakdownLine,
    HealthQuoteInputs,
    HealthRateTable,
    Ineligible,
    LifeQuoteInputs,
    LifeRateTable,
    QuoteResult,
    QuoteVariant,
    RatingOutcome,
)

ENGINE_VERSION = "2026.11.1"
_ONE = Decimal(1)
_THOUSAND = Decimal(1000)


class RatingError(ValueError):
    """The inputs or the table cannot be rated (a seed or programming error, not ineligibility)."""


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _band_for(age: int, bands: list[str]) -> str:
    for label in bands:
        if AgeBand.parse(label).contains(age):
            return label
    raise RatingError(f"no age band covers age {age}")


def _format_money(minor: int, currency: str) -> str:
    # Breakdown labels are plain English for traces; customer-facing formatting is done by the
    # channel with the conversation locale (ADR-0013).
    return f"{currency} {Decimal(minor) / 100:,.2f}"


def rate_life(inputs: LifeQuoteInputs, table: LifeRateTable, *, currency: str) -> RatingOutcome:
    reasons: list[str] = []
    if not table.min_issue_age <= inputs.age <= table.max_issue_age:
        reasons.append(f"age_out_of_range: issue ages {table.min_issue_age}-{table.max_issue_age}")
    if not table.min_coverage_minor <= inputs.coverage_minor <= table.max_coverage_minor:
        reasons.append(
            "coverage_out_of_range: "
            f"{_format_money(table.min_coverage_minor, currency)} to "
            f"{_format_money(table.max_coverage_minor, currency)}"
        )
    if reasons:
        return Ineligible(reasons=tuple(reasons), engine_version=ENGINE_VERSION)

    band = _band_for(inputs.age, list(table.rate_per_1000))
    units = Decimal(inputs.coverage_minor) / _THOUSAND
    variants: list[QuoteVariant] = []
    headline_breakdown: list[BreakdownLine] = []
    for status, label in (("non_smoker", "Non-smoker"), ("smoker", "Smoker")):
        rate = table.rate_per_1000[band][status]  # type: ignore[index]
        risk_premium = units * rate
        total = _round_minor(risk_premium + table.policy_fee_minor)
        variants.append(QuoteVariant(key=status, label=label, premium_minor=total))
        if status == "non_smoker":
            headline_breakdown = [
                BreakdownLine(
                    label=f"Cover {_format_money(inputs.coverage_minor, currency)} at "
                    f"{rate} per 1,000 per month (age band {band}, non-smoker)",
                    amount_minor=_round_minor(risk_premium),
                    factor=rate,
                ),
                BreakdownLine(label="Policy fee", amount_minor=table.policy_fee_minor),
            ]
    headline = variants[0].premium_minor
    return QuoteResult(
        premium_minor=headline,
        currency=currency,
        billing_period="monthly",
        monthly_equivalent_minor=headline,
        breakdown=tuple(headline_breakdown),
        variants=tuple(variants),
        engine_version=ENGINE_VERSION,
    )


def rate_health(
    inputs: HealthQuoteInputs, table: HealthRateTable, *, currency: str
) -> RatingOutcome:
    reasons: list[str] = []
    if not table.min_age <= inputs.age <= table.max_age:
        reasons.append(f"age_out_of_range: ages {table.min_age}-{table.max_age}")
    if inputs.dependents > table.max_dependents:
        reasons.append(f"too_many_dependents: at most {table.max_dependents}")
    if reasons:
        return Ineligible(reasons=tuple(reasons), engine_version=ENGINE_VERSION)

    band = _band_for(inputs.age, list(table.base_minor))
    base = Decimal(table.base_minor[band])
    region_factor = table.region_factor[inputs.region]
    adjusted = base * region_factor
    dependents_total = inputs.dependents * table.dependent_minor
    total = _round_minor(adjusted + dependents_total)
    breakdown = (
        BreakdownLine(label=f"Base premium (age band {band})", amount_minor=int(base)),
        BreakdownLine(
            label=f"Region adjustment ({inputs.region})",
            amount_minor=_round_minor(adjusted),
            factor=region_factor,
        ),
        BreakdownLine(
            label=f"Dependents ({inputs.dependents} x "
            f"{_format_money(table.dependent_minor, currency)})",
            amount_minor=dependents_total,
        ),
    )
    return QuoteResult(
        premium_minor=total,
        currency=currency,
        billing_period="monthly",
        monthly_equivalent_minor=total,
        breakdown=breakdown,
        engine_version=ENGINE_VERSION,
    )


def _claims_band(claims: int) -> str:
    return "2+" if claims >= 2 else str(claims)


def rate_auto(inputs: AutoQuoteInputs, table: AutoRateTable, *, currency: str) -> RatingOutcome:
    reasons: list[str] = []
    if not table.min_driver_age <= inputs.driver_age <= table.max_driver_age:
        reasons.append(
            f"driver_age_out_of_range: ages {table.min_driver_age}-{table.max_driver_age}"
        )
    if inputs.claims_last_5y > table.max_claims_last_5y:
        reasons.append(f"too_many_claims: at most {table.max_claims_last_5y} in 5 years")
    if reasons:
        return Ineligible(reasons=tuple(reasons), engine_version=ENGINE_VERSION)

    band = _band_for(inputs.driver_age, list(table.driver_age_factor))
    claims_band = _claims_band(inputs.claims_last_5y)
    steps: list[tuple[str, Decimal]] = [
        (f"Driver age {band}", table.driver_age_factor[band]),
        (f"Vehicle class {inputs.vehicle_class}", table.vehicle_class_factor[inputs.vehicle_class]),
        (
            f"Annual mileage {inputs.annual_mileage_band}",
            table.mileage_factor[inputs.annual_mileage_band],
        ),
        (f"Claims in last 5 years: {claims_band}", table.claims_factor[claims_band]),  # type: ignore[index]
    ]
    running = Decimal(table.base_annual_minor)
    breakdown = [BreakdownLine(label="Base annual premium", amount_minor=table.base_annual_minor)]
    for label, factor in steps:
        running *= factor
        breakdown.append(
            BreakdownLine(
                label=f"{label} (x{factor})", amount_minor=_round_minor(running), factor=factor
            )
        )
    annual = _round_minor(running)
    monthly = _round_minor(Decimal(annual) / 12)
    period: BillingPeriod = "annual"
    return QuoteResult(
        premium_minor=annual,
        currency=currency,
        billing_period=period,
        monthly_equivalent_minor=monthly,
        breakdown=tuple(breakdown),
        engine_version=ENGINE_VERSION,
    )


def rate(
    inputs: LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs,
    table: LifeRateTable | HealthRateTable | AutoRateTable,
    *,
    currency: str,
) -> RatingOutcome:
    """Rate `inputs` against `table`. The input line must match the table line."""
    if isinstance(inputs, LifeQuoteInputs) and isinstance(table, LifeRateTable):
        return rate_life(inputs, table, currency=currency)
    if isinstance(inputs, HealthQuoteInputs) and isinstance(table, HealthRateTable):
        return rate_health(inputs, table, currency=currency)
    if isinstance(inputs, AutoQuoteInputs) and isinstance(table, AutoRateTable):
        return rate_auto(inputs, table, currency=currency)
    raise RatingError(f"inputs for line {inputs.line!r} do not match table line {table.line!r}")
