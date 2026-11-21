"""Load and validate the fictional catalog in data/seed/.

The loader validates everything before anything touches the database: an invalid rate table is
a seed error that fails loudly, never a silent default (docs/design/catalog-and-rating.md).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from policypilot.domain.rating.models import BillingPeriod, ProductLine, RateTable


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgencySeed(_Strict):
    name: str
    locale: str
    currency: str = Field(min_length=3, max_length=3)
    inbox: str


class CarrierSeed(_Strict):
    code: str
    name: str
    financial_strength: str
    website: str

    @model_validator(mode="after")
    def _fictional_domain(self) -> CarrierSeed:
        if not self.website.startswith("https://") or not self.website.endswith(".example.com"):
            raise ValueError(f"carrier {self.code}: websites must use an example.com domain")
        return self


class ProductTextSeed(_Strict):
    name: str
    summary: str
    highlights: list[str]
    exclusions_summary: str


class RateTableSeed(_Strict):
    version: str
    effective_from: date
    factors: RateTable


class ProductSeed(_Strict):
    code: str
    carrier: str
    line: ProductLine
    currency: str = Field(min_length=3, max_length=3)
    billing_period: BillingPeriod
    attributes: dict[str, Any]
    texts: dict[str, ProductTextSeed]
    rate_table: RateTableSeed

    @model_validator(mode="after")
    def _consistent(self) -> ProductSeed:
        if self.rate_table.factors.line != self.line:
            raise ValueError(f"product {self.code}: rate table line does not match product line")
        if "en" not in self.texts:
            raise ValueError(f"product {self.code}: English texts are required (fallback locale)")
        expected: dict[str, BillingPeriod] = {
            "life": "monthly",
            "health": "monthly",
            "auto": "annual",
        }
        if self.billing_period != expected[self.line]:
            raise ValueError(
                f"product {self.code}: {self.line} products bill {expected[self.line]}"
            )
        return self


class TemplateSeed(_Strict):
    key: str
    channel: Literal["web", "whatsapp"]
    locale: str
    body: str
    params: list[str] = []
    meta_template_name: str | None = None
    meta_language_code: str | None = None
    category: Literal["utility", "marketing", "authentication"] | None = None


class CatalogSeed(_Strict):
    agency: AgencySeed
    carriers: list[CarrierSeed]
    products: list[ProductSeed]
    templates: list[TemplateSeed]

    @model_validator(mode="after")
    def _references(self) -> CatalogSeed:
        carrier_codes = {c.code for c in self.carriers}
        seen: set[str] = set()
        for product in self.products:
            if product.carrier not in carrier_codes:
                raise ValueError(f"product {product.code}: unknown carrier {product.carrier}")
            if product.code in seen:
                raise ValueError(f"duplicate product code {product.code}")
            seen.add(product.code)
        keys = [(t.key, t.channel, t.locale) for t in self.templates]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate (key, channel, locale) in message templates")
        return self

    def product(self, code: str) -> ProductSeed:
        for product in self.products:
            if product.code == code:
                return product
        raise KeyError(code)


def default_seed_dir() -> Path:
    """data/seed relative to the repository root, or SEED_DIR inside containers."""
    import os

    configured = os.environ.get("SEED_DIR")
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "seed"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("data/seed not found; set SEED_DIR")


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_catalog(seed_dir: Path | None = None) -> CatalogSeed:
    root = seed_dir or default_seed_dir()
    carriers = _read_yaml(root / "carriers.yaml")
    products = _read_yaml(root / "products.yaml")
    templates = _read_yaml(root / "message_templates.yaml")
    return TypeAdapter(CatalogSeed).validate_python(
        {
            "agency": carriers["agency"],
            "carriers": carriers["carriers"],
            "products": products["products"],
            "templates": templates["templates"],
        }
    )
