"""Golden and property tests for the deterministic rating engine (ADR-0003)."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from policypilot.domain.rating.engine import RatingError, rate
from policypilot.domain.rating.models import (
    HealthQuoteInputs,
    Ineligible,
    LifeQuoteInputs,
    QuoteInputs,
    QuoteResult,
)
from policypilot.seed.loader import CatalogSeed, load_catalog


@functools.cache
def cached_catalog() -> CatalogSeed:
    return load_catalog()


GOLDEN = yaml.safe_load((Path(__file__).parent / "golden.yaml").read_text(encoding="utf-8"))
INPUTS: TypeAdapter[Any] = TypeAdapter(QuoteInputs)


@pytest.fixture(scope="module")
def catalog() -> CatalogSeed:
    return load_catalog()


def test_seed_has_13_products_from_4_carriers(catalog: CatalogSeed) -> None:
    assert len(catalog.products) == 13
    assert {c.code for c in catalog.carriers} == {"KESTREL", "SOLACE", "TOLLGATE", "EVERGREEN"}


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["id"] for c in GOLDEN["cases"]])
def test_golden(case: dict[str, Any], catalog: CatalogSeed) -> None:
    product = catalog.product(case["product"])
    outcome = rate(
        INPUTS.validate_python(case["inputs"]),
        product.rate_table.factors,
        currency=product.currency,
    )
    if "ineligible" in case:
        assert isinstance(outcome, Ineligible)
        assert [r.split(":")[0] for r in outcome.reasons] == case["ineligible"]
        return
    assert isinstance(outcome, QuoteResult)
    assert outcome.premium_minor == case["premium_minor"]
    if "variants" in case:
        assert {v.key: v.premium_minor for v in outcome.variants} == case["variants"]
    if "monthly_equivalent_minor" in case:
        assert outcome.monthly_equivalent_minor == case["monthly_equivalent_minor"]
    assert outcome.premium_minor in outcome.all_amounts_minor()


def test_mismatched_line_is_a_programming_error(catalog: CatalogSeed) -> None:
    table = catalog.product("SOLACE-SILVER").rate_table.factors
    with pytest.raises(RatingError):
        rate(LifeQuoteInputs(age=40, coverage_minor=10_000_000), table, currency="USD")


def test_unknown_input_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="extra"):
        INPUTS.validate_python({"line": "life", "age": 40, "coverage_minor": 1, "sex": "f"})


@given(
    age=st.integers(min_value=18, max_value=59),
    coverage=st.integers(min_value=5_000_000, max_value=150_000_000),
)
def test_life_premium_is_positive_and_smoker_never_cheaper(age: int, coverage: int) -> None:
    product = cached_catalog().product("EVERGREEN-TERM-20")
    outcome = rate(
        LifeQuoteInputs(age=age, coverage_minor=coverage),
        product.rate_table.factors,
        currency="USD",
    )
    assert isinstance(outcome, QuoteResult)
    variants = {v.key: v.premium_minor for v in outcome.variants}
    assert 0 < variants["non_smoker"] <= variants["smoker"]


@given(
    age=st.integers(min_value=18, max_value=63), dependents=st.integers(min_value=0, max_value=5)
)
def test_health_premium_never_decreases_with_age_or_dependents(age: int, dependents: int) -> None:
    table = cached_catalog().product("SOLACE-SILVER").rate_table.factors

    def premium(a: int, d: int) -> int:
        outcome = rate(
            HealthQuoteInputs(age=a, region="central", dependents=d), table, currency="USD"
        )
        assert isinstance(outcome, QuoteResult)
        return outcome.premium_minor

    assert premium(age + 1, dependents) >= premium(age, dependents)
    assert premium(age, dependents + 1) > premium(age, dependents)
