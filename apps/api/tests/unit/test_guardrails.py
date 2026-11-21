from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from policypilot.agents.guardrails import (
    TurnFacts,
    banned_claims,
    literal_amounts,
    render,
    validate,
)

QUOTE_ID = str(uuid.uuid4())


def facts(**overrides: Any) -> TurnFacts:
    base: dict[str, Any] = {
        "quote_ids": {QUOTE_ID},
        "chunk_ids": {11, 12},
        "chunk_products": {"11": "p-1", "12": "p-2"},
        "allowed_amounts": {300000},
    }
    base.update(overrides)
    return TurnFacts(**base)


def test_known_placeholders_and_attribute_amounts_pass() -> None:
    text = f"Kestrel is {{{{quote:{QUOTE_ID}}}}} with a $3,000 deductible."
    assert validate(text, facts(), "web").ok


def test_unknown_placeholder_fails() -> None:
    result = validate(f"Price: {{{{quote:{uuid.uuid4()}}}}}", facts(), "web")
    assert any(p.startswith("unknown_quote_placeholder") for p in result.problems)


def test_literal_premiums_fail_even_if_plausible() -> None:
    result = validate("It costs $75.00 per month.", facts(), "web")
    assert result.problems == ["unverified_premium:7500"]


def test_amount_parsing_handles_suffixes_and_words() -> None:
    assert literal_amounts("$500k cover, $1.2m limit, 250 dollars") == [50000000, 120000000, 25000]


def test_citations_must_come_from_this_turn_and_product() -> None:
    assert not validate("See [doc:99].", facts(), "web").ok
    wrong = validate("See [doc:12].", facts(focus_product_ids=frozenset({"p-1"})), "web")
    assert wrong.problems == ["wrong_product_citation:12"]


def test_policy_answers_need_a_citation() -> None:
    result = validate("Suicide is excluded.", facts(policy_question=True), "web")
    assert "policy_answer_without_citation" in result.problems
    assert validate("Suicide is excluded [doc:11].", facts(policy_question=True), "web").ok


def test_banned_claims_respect_negation() -> None:
    assert banned_claims("Approval is guaranteed and you are covered.") == [
        "guaranteed",
        "you are covered",
    ]
    assert banned_claims("This price is not guaranteed.") == []


def test_render_fills_prices_and_numbers_citations() -> None:
    quote = SimpleNamespace(
        premium_minor=7500,
        monthly_equivalent_minor=7500,
        currency="USD",
        billing_period="monthly",
        variants={
            "items": [
                {"key": "non_smoker", "premium_minor": 7500},
                {"key": "smoker", "premium_minor": 17300},
            ]
        },
        expires_at=datetime.now(UTC),
    )
    chunks = {11: {"documentTitle": "Kestrel", "section": "4.1 Exclusions", "pages": "1"}}
    text, citations = render(
        f"{{{{quote:{QUOTE_ID}}}}} [doc:11] and again [doc:11]",
        {QUOTE_ID: quote},  # type: ignore[dict-item]
        chunks,
        "en",
    )
    assert text == (
        "$75.00 per month (estimate) for non-smokers ($173.00 per month for smokers) [1] "
        "and again [1]"
    )
    assert citations == [
        {"chunkId": 11, "documentTitle": "Kestrel", "section": "4.1 Exclusions", "pages": "1"}
    ]


def test_annual_premiums_show_a_labelled_monthly_equivalent() -> None:
    quote = SimpleNamespace(
        premium_minor=151800,
        monthly_equivalent_minor=12650,
        currency="USD",
        billing_period="annual",
        variants={"items": []},
    )
    text, _ = render(f"{{{{quote:{QUOTE_ID}}}}}", {QUOTE_ID: quote}, {}, "en")  # type: ignore[dict-item]
    assert text == "$1,518.00 per year (about $126.50 per month, estimate)"


# Review 02, finding 1: a price placed next to another product.
def test_a_price_next_to_another_products_name_is_rejected() -> None:
    from policypilot.services.catalog import ProductView

    def product(code: str, carrier: str, name: str) -> ProductView:
        return ProductView(
            id=uuid.uuid5(uuid.NAMESPACE_OID, code),
            code=code,
            line="life",
            carrier_code=code,
            carrier_name=carrier,
            name=name,
            summary="",
            highlights=[],
            exclusions_summary="",
            attributes={},
            currency="USD",
            billing_period="monthly",
            active=True,
        )

    kestrel = product("K20", "Kestrel Life Assurance", "Kestrel Term Life 20")
    evergreen = product("E20", "Evergreen Mutual", "Evergreen Term Life 20 Flex")
    checks = facts(quote_products={QUOTE_ID: str(evergreen.id)}, catalog=(kestrel, evergreen))
    placeholder = f"{{{{quote:{QUOTE_ID}}}}}"
    wrong = validate(f"- Kestrel Term Life 20: {placeholder}", checks, "web")
    assert wrong.problems == [f"misattributed_quote:{QUOTE_ID}"]
    right = f"- Evergreen Term Life 20 Flex by Evergreen Mutual: {placeholder}"
    assert validate(right, checks, "web").ok
    assert validate(f"Estimate: {placeholder}", checks, "web").ok  # no product named


def test_a_question_about_a_guarantee_is_not_a_guarantee() -> None:
    from policypilot.agents.guardrails import banned_claims

    assert banned_claims("On whether the price is guaranteed: no, quotes aren't guaranteed.") == []
    assert banned_claims("Your price is guaranteed.") == ["guaranteed"]


def test_the_history_price_marker_never_reaches_a_customer() -> None:
    from policypilot.agents.guardrails import QUOTED_PRICE, TurnFacts, validate

    facts = TurnFacts(quote_ids=set(), chunk_ids=set(), chunk_products={}, allowed_amounts=set())
    result = validate(f"It is {QUOTED_PRICE}.", facts, "web")
    assert result.problems == ["history_price_marker"]
