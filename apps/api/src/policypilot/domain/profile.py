"""The needs-analysis profile and the required fields per line (docs/design/agent-design.md).

The profile never holds smoking status, gender, health conditions, identifiers or contact details.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from policypilot.domain.rating.models import (
    AutoQuoteInputs,
    HealthQuoteInputs,
    LifeQuoteInputs,
    MileageBand,
    ProductLine,
    Region,
    VehicleClass,
)

TermYears = Literal[10, 20, 30]


class CustomerProfile(BaseModel):
    """All optional; merged turn by turn from validated extraction deltas."""

    model_config = ConfigDict(extra="forbid")

    lines: list[ProductLine] = Field(default_factory=list)
    age: int | None = Field(default=None, ge=0, le=120)
    household_size: int | None = Field(default=None, ge=1, le=15)
    dependents: int | None = Field(default=None, ge=0, le=12)
    monthly_budget_minor: int | None = Field(default=None, gt=0)
    region: Region | None = None
    coverage_goal_minor: int | None = Field(default=None, gt=0)
    term_years: TermYears | None = None
    tier_preference: Literal["bronze", "silver", "gold"] | None = None
    driver_age: int | None = Field(default=None, ge=0, le=120)
    vehicle_class: VehicleClass | None = None
    annual_mileage_band: MileageBand | None = None
    claims_last_5y: int | None = Field(default=None, ge=0, le=20)
    coverage_level: Literal["liability", "standard", "comprehensive"] | None = None

    def merge(self, delta: CustomerProfile) -> CustomerProfile:
        data = self.model_dump()
        for key, value in delta.model_dump(exclude_unset=True).items():
            if value is None:
                continue
            if key == "lines":
                data["lines"] = sorted(set(data["lines"]) | set(value))
            else:
                data[key] = value
        return CustomerProfile.model_validate(data)


REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "life": ("age", "coverage_goal_minor", "term_years"),
    "health": ("age", "region", "dependents"),
    "auto": ("driver_age", "vehicle_class", "annual_mileage_band", "claims_last_5y"),
}

# Bounds used when asking (the rating engine still reports ineligibility precisely).
FIELD_HINTS: dict[str, str] = {
    "age": "age (18-65 for life, 18-64 for health)",
    "coverage_goal_minor": "how much life cover you want",
    "term_years": "how many years of cover: 10, 20 or 30",
    "region": "your region: north, central or south",
    "dependents": "how many dependants to include (0-8)",
    "driver_age": "the main driver's age",
    "vehicle_class": "the type of car: economy, standard, premium or SUV",
    "annual_mileage_band": "yearly mileage: low (under 8,000), medium or high (over 15,000)",
    "claims_last_5y": "how many claims in the last 5 years",
}


def missing_fields(profile: CustomerProfile, line: ProductLine) -> list[str]:
    return [name for name in REQUIRED_FIELDS[line] if getattr(profile, name) is None]


def rating_inputs(
    profile: CustomerProfile, line: ProductLine, *, coverage_override_minor: int | None = None
) -> LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs:
    """Build validated rating inputs from the profile only (the model cannot inject numbers)."""
    missing = missing_fields(profile, line)
    if missing:
        raise MissingFieldsError(missing)
    if line == "life":
        assert profile.age is not None and profile.coverage_goal_minor is not None
        return LifeQuoteInputs(
            age=profile.age,
            coverage_minor=coverage_override_minor or profile.coverage_goal_minor,
        )
    if line == "health":
        assert profile.age is not None and profile.region is not None
        return HealthQuoteInputs(
            age=profile.age, region=profile.region, dependents=profile.dependents or 0
        )
    assert profile.driver_age is not None and profile.vehicle_class is not None
    assert profile.annual_mileage_band is not None and profile.claims_last_5y is not None
    return AutoQuoteInputs(
        driver_age=profile.driver_age,
        vehicle_class=profile.vehicle_class,
        annual_mileage_band=profile.annual_mileage_band,
        claims_last_5y=profile.claims_last_5y,
    )


class MissingFieldsError(ValueError):
    def __init__(self, fields: list[str]) -> None:
        super().__init__("missing_fields: " + ", ".join(fields))
        self.fields = fields
