"""Number accounting in the output guardrail (review 02, finding 1, second round).

Every number in a reply must be accounted for by the turn's facts and by what kind of number it
is. A price cannot pass by rewording: no currency sign, no "dollars", or written in words.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from policypilot.agents.guardrails import TurnFacts, validate
from policypilot.agents.numbers import mentions
from policypilot.services.catalog import ProductView


def product(
    code: str, carrier: str, name: str, attributes: dict[str, Any] | None = None
) -> ProductView:
    return ProductView(
        id=uuid.uuid5(uuid.NAMESPACE_OID, code),
        code=code,
        line="life",
        carrier_code=carrier.split()[0].upper(),
        carrier_name=carrier,
        name=name,
        summary="",
        highlights=[],
        exclusions_summary="",
        attributes=attributes or {},
        currency="USD",
        billing_period="monthly",
        active=True,
    )


CATALOG = (
    product("KESTREL-TERM-20", "Kestrel Life Assurance", "Kestrel Term Life 20"),
    product("EVERGREEN-TERM-20", "Evergreen Mutual", "Evergreen Term Life 20 Flex"),
)


def reviewer_facts(**overrides: Any) -> TurnFacts:
    """The independent check's setup: no quotes, the budget ($80) both allowed and the budget."""
    base: dict[str, Any] = {
        "quote_ids": set(),
        "chunk_ids": set(),
        "chunk_products": {},
        "allowed_amounts": {8000},
        "budget_minor": 8000,
        "catalog": CATALOG,
    }
    base.update(overrides)
    return TurnFacts(**base)


@pytest.mark.parametrize(
    "reply",
    [
        "Kestrel Term Life is $80.",
        "Kestrel Term Life: $80",
        "You'd pay 80 a month for Kestrel Term Life.",
        "Kestrel Term Life would be 80 per month.",
        "The monthly premium for Kestrel Term Life is 73.",
        "That plan runs about 80 bucks a month.",
        "It is eighty a month.",
        "Kestrel Term Life is under $80 a month.",
    ],
)
def test_a_price_cannot_pass_by_rewording(reply: str) -> None:
    result = validate(reply, reviewer_facts(), "web")
    assert not result.ok, reply


@pytest.mark.parametrize(
    "reply",
    [
        "It costs eighty dollars.",
        "Kestrel Term Life comes to USD 80.",
        "Kestrel Term Life is 80$ monthly.",
        "Kestrel Term Life is seventy-three dollars a month.",
        "Pay 0.8k monthly for Kestrel Term Life.",
        "Kestrel is 80bucks.",
    ],
)
def test_more_rewordings_are_rejected(reply: str) -> None:
    assert not validate(reply, reviewer_facts(), "web").ok, reply


def legit_facts() -> TurnFacts:
    """A turn after quoting: profile 40 years, $500k cover, 20-year term, $80 budget."""
    return TurnFacts(
        quote_ids=set(),
        chunk_ids={12},
        chunk_products={"12": str(CATALOG[0].id)},
        allowed_amounts={300_000},  # a $3,000 deductible attribute
        budget_minor=8000,
        coverage_minor=50_000_000,
        plain_numbers=frozenset(
            {
                Decimal(40),
                Decimal(20),
                Decimal(3000),
                Decimal("4.1"),
                Decimal(2),
                Decimal(18),
                Decimal(65),
            }
        ),
        catalog=CATALOG,
    )


@pytest.mark.parametrize(
    "reply",
    [
        # ages, term lengths, coverage
        "For a 40-year-old with $500,000 of cover over a 20-year term, these fit:",
        "Life cover is available from age 18 to 65.",
        # deductibles (a product attribute)
        "Kestrel Term Life 20 has a $3,000 deductible.",
        "The deductible is 3,000.",
        # citation numbers and clause numbers from the retrieved text
        "Clause 4.1 excludes suicide within 2 years of the issue date [doc:12].",
        # dates and years
        "Estimates are valid until Dec 20, 2026.",
        "The wording was updated in 2026.",
        # counts
        "I found three products that fit, and 2 of them are term life.",
        "Here is one option.",
        # the budget in a sentence about the budget
        "That fits your monthly budget of $80.",
        "You said you can afford $80 a month.",
        # product names that contain numbers are not numbers
        "Kestrel Term Life 20 and Evergreen Term Life 20 Flex both match.",
    ],
)
def test_legitimate_numbers_still_pass(reply: str) -> None:
    result = validate(reply, legit_facts(), "web")
    assert result.ok, (reply, result.problems)


def test_the_budget_is_never_a_statement_about_a_product() -> None:
    result = validate("Kestrel Term Life fits your budget of $80 a month.", legit_facts(), "web")
    assert result.problems == ["budget_outside_budget_sentence:8000"]


def test_unknown_numbers_are_rejected_even_without_money_words() -> None:
    result = validate("Kestrel Term Life: 73", legit_facts(), "web")
    assert result.problems == ["unverified_number:73"]
    assert not validate("It pays out 95% of claims.", legit_facts(), "web").ok


def test_templated_drafts_skip_number_accounting() -> None:
    reply = "Your excess is $250."  # e.g. an agent's note inside a decision template
    assert not validate(reply, reviewer_facts(), "web").ok
    assert validate(reply, reviewer_facts(check_numbers=False), "web").ok


def test_the_extractor_misses_nothing() -> None:
    found = [
        (m.value, m.currency, m.percent, m.words)
        for m in mentions(
            "$80, 80 bucks, eighty dollars, 1,200.50, 18,65, 20%, 80k and one hundred and five"
        )
    ]
    assert found == [
        (Decimal(80), True, False, False),
        (Decimal(80), True, False, False),
        (Decimal(80), True, False, True),
        (Decimal("1200.50"), False, False, False),
        (Decimal(18), False, False, False),
        (Decimal(65), False, False, False),
        (Decimal(20), False, True, False),
        (Decimal(80000), False, False, False),
        (Decimal(105), False, False, True),
    ]


def policy_facts() -> TurnFacts:
    """A policy answer: the retrieved clauses mention 31 days, 2 years, 4.1, 2.4 and 65."""
    return TurnFacts(
        quote_ids=set(),
        chunk_ids={5},
        chunk_products={},
        allowed_amounts=set(),
        budget_minor=8000,
        plain_numbers=frozenset(
            {Decimal(31), Decimal(2), Decimal("4.1"), Decimal("2.4"), Decimal(65), Decimal(30)}
        ),
        catalog=CATALOG,
    )


@pytest.mark.parametrize(
    "reply",
    [
        # durations and clause numbers in sentences about payments are quantities, not prices
        "If a payment is missed, your cover stays in force for 31 days after the due date [doc:5].",
        "Clause 4.1: we will not pay the Sum Insured for suicide within 2 years [doc:5].",
        "Under clause 2.4, if you die during the grace period we pay the Sum Insured [doc:5].",
        "Premiums stop at age 65 [doc:5].",
        "The conversion must be requested at least 30 days before your 65th birthday [doc:5].",
        "Kestrel Term Life 20 pays out within 1 year of a valid claim [doc:5].",
        "Your first payment is due on Dec 20, 2026.",
        "Payments run for a 30-year term at most.",
    ],
)
def test_quantities_in_payment_sentences_pass(reply: str) -> None:
    result = validate(reply, policy_facts(), "web")
    assert result.ok, (reply, result.problems)


@pytest.mark.parametrize(
    "reply",
    [
        "Kestrel Term Life costs 31 a month.",  # a fact number stated as a price
        "The premium for Kestrel Term Life is 65.",
        "Kestrel Term Life costs $31 days.",  # a currency sign makes it money
        "Kestrel Term Life is 80 per month at age 65.",
        "The premium is 80 months.",  # a unit does not account for an unknown number
        "You pay 73 for clause 4.1 cover.",
    ],
)
def test_units_and_labels_do_not_launder_a_price(reply: str) -> None:
    assert not validate(reply, policy_facts(), "web").ok, reply


def test_known_limit_the_budget_can_be_repeated_in_a_budget_sentence() -> None:
    """Documented in the README (residual limit): no product is named, so the budget passes."""
    assert validate("It fits your budget of $80, since it is $80.", reviewer_facts(), "web").ok
    named = validate(
        "Kestrel Term Life fits your budget of $80, since it is $80.", reviewer_facts(), "web"
    )
    assert not named.ok


def health_facts() -> TurnFacts:
    """Health plans after quoting: deductibles $3,000 and $6,000, out-of-pocket $7,000 and $9,000,
    coinsurance 30% and 40%, ages 18 to 64, a $500,000 cover goal for the life lines."""
    return TurnFacts(
        quote_ids=set(),
        chunk_ids=set(),
        chunk_products={},
        allowed_amounts={300_000, 600_000, 700_000, 900_000},
        budget_minor=8000,
        coverage_minor=50_000_000,
        plain_numbers=frozenset(
            {Decimal(n) for n in (3000, 6000, 7000, 9000, 30, 40, 18, 64, 10, 20)}
        ),
        catalog=CATALOG,
    )


@pytest.mark.parametrize(
    "reply",
    [
        # Sentences Claude wrote in the first live run, rejected before the marks existed.
        "This has the lowest monthly price, but a narrow network, a $6,000 deductible and 40% "
        "coinsurance.",
        "After the deductible you pay 40% of costs, up to a $9,000 out-of-pocket maximum.",
        "The deductible is $6,000, you pay 40% coinsurance and the out-of-pocket maximum is "
        "$9,000.",
        "This costs the most each month but has the lowest deductible ($3,000).",
        "The two prices are close, so the features may matter more than the cost.",
        "I can compare these two side by side so you can see what sets them apart besides price.",
        "Here are three that cover the range from lowest monthly cost to most predictable bills.",
        "I can't give you a quote for $500,000 over 20 years.",
        "I priced $500,000 of 20-year term life cover for age 40, and two products fit.",
        "All three Kestrel term policies (10, 20 and 30 year) won't pay for suicide within 2 "
        "years.",
        "These plans accept applicants aged 18 to 64, so I can't give you prices or recommend one.",
        "I found two 20-year term life options, both priced for $500,000 of cover at age 40.",
        # A deductible in plain words (the second live run, WhatsApp, auto).
        "It covers your own car in a crash, but you pay the first $3,000 of a claim.",
        "On a crash claim you pay the first $6,000 instead.",
        # Product versions in brackets (the second full live run).
        "All three Kestrel term policies (10, 20 and 30) don't pay out for suicide within 2 years.",
        "The main difference is the deductible, which is what you'd pay towards a claim: $3,000 "
        "with Tollgate and $6,000 with Evergreen.",
    ],
)
def test_marked_facts_pass_in_price_sentences(reply: str) -> None:
    result = validate(reply, health_facts(), "web")
    assert result.ok, (reply, result.problems)


@pytest.mark.parametrize(
    "reply",
    [
        # A true attribute amount or count, stated as a price, is still a price.
        "Kestrel Term Life costs $3,000.",
        "The premium for Kestrel Term Life is $3,000.",
        "Kestrel Term Life is $3,000 a month.",
        "You pay $6,000 a year for Kestrel Term Life.",
        "Kestrel Term Life costs two.",
        "Kestrel Term Life costs the two.",
        "Kestrel Term Life: 9 is the price.",
        "The premium is about 20 for Kestrel Term Life.",
        "Its premium runs 18 to 64.",
        "Kestrel Term Life comes to $500,000 a month.",
        "You pay $3,000 a month per claim for Kestrel Term Life.",
        "Your monthly premium with this deductible is: Kestrel Term Life $3,000.",
        "With its deductible, you pay $3,000 for Kestrel Term Life.",
        "Kestrel Term Life plans (80) cost less.",
    ],
)
def test_marks_do_not_launder_a_price(reply: str) -> None:
    assert not validate(reply, health_facts(), "web").ok, reply


def test_the_evidence_names_the_offending_sentence() -> None:
    result = validate(
        "Here are two options. Kestrel Term Life costs $3,000.", health_facts(), "web"
    )
    assert result.problems == ["unverified_premium:300000"]
    assert result.evidence == ["Kestrel Term Life costs $3,000"]
