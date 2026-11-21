"""Scenario files: `evals/scenarios/*.yaml`, each a list of scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

Group = Literal["needs_analysis", "products", "quotes", "policy_qa", "safety", "handoff"]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str
    action: str | None = None


class Expect(BaseModel):
    """Deterministic expectations, checked against the final turn (and invariants on every turn)."""

    model_config = ConfigDict(extra="forbid")
    route: list[str] | None = None
    tools_called: list[str] = Field(default_factory=list)
    tools_not_called: list[str] = Field(default_factory=list)
    quotes_min: int | None = None
    quotes_max: int | None = None
    quote_products: list[str] = Field(default_factory=list)
    comparison: bool | None = None
    reply_must_include: list[str] = Field(default_factory=list)
    reply_must_not_include: list[str] = Field(default_factory=list)
    reply_any_of: list[str] = Field(default_factory=list)
    questions_max: int | None = None
    citations_min: int | None = None
    citations_max: int | None = None
    citation_products: list[str] = Field(default_factory=list)
    guardrail_actions_include: list[str] = Field(default_factory=list)
    guardrail_actions_exclude: list[str] = Field(default_factory=list)
    status: str | None = None
    review_task: Literal["application_review", "handoff"] | None = None
    lead: Literal["application", "contact_request"] | None = None
    stored_text_must_not_include: list[str] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    group: Group
    description: str
    turns: list[Turn]
    expect: Expect
    judge: list[str] = Field(default_factory=list, description="Rubric items for the live judge")

    @field_validator("turns", mode="before")
    @classmethod
    def _plain_strings(cls, value: Any) -> Any:
        return [{"user": t} if isinstance(t, str) else t for t in value]


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            scenarios.append(Scenario.model_validate(raw))
    ids = [s.id for s in scenarios]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario ids: {sorted(duplicates)}")
    return scenarios
