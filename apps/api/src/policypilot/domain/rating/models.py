"""Typed inputs, rate tables and results for the deterministic rating engine (ADR-0003)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProductLine = Literal["life", "health", "auto"]
BillingPeriod = Literal["monthly", "annual"]
Region = Literal["north", "central", "south"]
SmokerStatus = Literal["non_smoker", "smoker"]
VehicleClass = Literal["economy", "standard", "premium", "suv"]
MileageBand = Literal["low", "medium", "high"]
ClaimsBand = Literal["0", "1", "2+"]

# A positive decimal written as a string in seed files, so no float ever touches a rate.
Rate = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=6)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- inputs


class LifeQuoteInputs(_Strict):
    line: Literal["life"] = "life"
    age: int = Field(ge=0, le=120)
    coverage_minor: int = Field(gt=0, description="Death benefit in minor units")


class HealthQuoteInputs(_Strict):
    line: Literal["health"] = "health"
    age: int = Field(ge=0, le=120)
    region: Region
    dependents: int = Field(default=0, ge=0, le=12)


class AutoQuoteInputs(_Strict):
    line: Literal["auto"] = "auto"
    driver_age: int = Field(ge=0, le=120)
    vehicle_class: VehicleClass
    annual_mileage_band: MileageBand
    claims_last_5y: int = Field(ge=0, le=20)


QuoteInputs = Annotated[
    LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs, Field(discriminator="line")
]


# --------------------------------------------------------------------------- rate tables


class AgeBand(_Strict):
    """An inclusive age band, e.g. 30-39. Bands are written as "30-39" or "65+"."""

    label: str
    low: int
    high: int | None

    @classmethod
    def parse(cls, label: str) -> AgeBand:
        if label.endswith("+"):
            return cls(label=label, low=int(label[:-1]), high=None)
        low, _, high = label.partition("-")
        return cls(label=label, low=int(low), high=int(high))

    def contains(self, age: int) -> bool:
        return age >= self.low and (self.high is None or age <= self.high)


class LifeRateTable(_Strict):
    line: Literal["life"] = "life"
    rate_per_1000: dict[str, dict[SmokerStatus, Rate]]
    policy_fee_minor: int = Field(ge=0)
    min_coverage_minor: int = Field(gt=0)
    max_coverage_minor: int = Field(gt=0)
    min_issue_age: int = Field(ge=0)
    max_issue_age: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> LifeRateTable:
        if self.min_coverage_minor > self.max_coverage_minor:
            raise ValueError("min_coverage_minor exceeds max_coverage_minor")
        for band, rates in self.rate_per_1000.items():
            AgeBand.parse(band)
            if set(rates) != {"non_smoker", "smoker"}:
                raise ValueError(f"band {band} must define non_smoker and smoker rates")
        return self


class HealthRateTable(_Strict):
    line: Literal["health"] = "health"
    base_minor: dict[str, int]
    region_factor: dict[Region, Rate]
    dependent_minor: int = Field(ge=0)
    max_dependents: int = Field(ge=0)
    min_age: int = Field(ge=0)
    max_age: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> HealthRateTable:
        for band in self.base_minor:
            AgeBand.parse(band)
        if set(self.region_factor) != {"north", "central", "south"}:
            raise ValueError("region_factor must define north, central and south")
        return self


class AutoRateTable(_Strict):
    line: Literal["auto"] = "auto"
    base_annual_minor: int = Field(gt=0)
    driver_age_factor: dict[str, Rate]
    vehicle_class_factor: dict[VehicleClass, Rate]
    mileage_factor: dict[MileageBand, Rate]
    claims_factor: dict[ClaimsBand, Rate]
    min_driver_age: int = Field(ge=0)
    max_driver_age: int = Field(gt=0)
    max_claims_last_5y: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> AutoRateTable:
        for band in self.driver_age_factor:
            AgeBand.parse(band)
        if set(self.vehicle_class_factor) != {"economy", "standard", "premium", "suv"}:
            raise ValueError("vehicle_class_factor must define every vehicle class")
        if set(self.mileage_factor) != {"low", "medium", "high"}:
            raise ValueError("mileage_factor must define low, medium and high")
        if set(self.claims_factor) != {"0", "1", "2+"}:
            raise ValueError("claims_factor must define 0, 1 and 2+")
        return self


RateTable = Annotated[LifeRateTable | HealthRateTable | AutoRateTable, Field(discriminator="line")]


# --------------------------------------------------------------------------- results


class BreakdownLine(_Strict):
    """One step of the calculation, shown to customers and agents."""

    label: str
    amount_minor: int | None = None
    factor: Decimal | None = None


class QuoteVariant(_Strict):
    key: str
    label: str
    premium_minor: int


class QuoteResult(_Strict):
    eligible: Literal[True] = True
    premium_minor: int
    currency: str
    billing_period: BillingPeriod
    monthly_equivalent_minor: int
    breakdown: tuple[BreakdownLine, ...]
    variants: tuple[QuoteVariant, ...] = ()
    engine_version: str

    def all_amounts_minor(self) -> set[int]:
        """Every amount a reply may legitimately mention for this quote (price-integrity guard)."""
        amounts = {self.premium_minor, self.monthly_equivalent_minor}
        amounts.update(v.premium_minor for v in self.variants)
        amounts.update(b.amount_minor for b in self.breakdown if b.amount_minor is not None)
        return amounts


class Ineligible(_Strict):
    eligible: Literal[False] = False
    reasons: tuple[str, ...]
    engine_version: str


RatingOutcome = QuoteResult | Ineligible
