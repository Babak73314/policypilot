"""Products named in a message (services/catalog.py): code, not a model, scopes policy searches."""

from __future__ import annotations

import uuid

import pytest

from policypilot.services.catalog import ProductView, mentioned_products


def product(code: str, line: str, carrier: str, name: str) -> ProductView:
    return ProductView(
        id=uuid.uuid5(uuid.NAMESPACE_OID, code),
        code=code,
        line=line,
        carrier_code=carrier.split()[0].upper(),
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


CATALOG = [
    product("KESTREL-TERM-10", "life", "Kestrel Life Assurance", "Kestrel Term Life 10"),
    product("KESTREL-TERM-20", "life", "Kestrel Life Assurance", "Kestrel Term Life 20"),
    product("EVERGREEN-TERM-20", "life", "Evergreen Mutual", "Evergreen Term Life 20 Flex"),
    product(
        "EVERGREEN-SILVER-FAMILY", "health", "Evergreen Mutual", "Evergreen Silver Family Health"
    ),
    product("SOLACE-GOLD", "health", "Solace Health Cooperative", "Solace Gold Complete"),
    product("SOLACE-SILVER", "health", "Solace Health Cooperative", "Solace Silver Choice"),
    product(
        "TOLLGATE-COMPREHENSIVE", "auto", "Tollgate Auto Insurance", "Tollgate Comprehensive Plus"
    ),
    product("TOLLGATE-STANDARD", "auto", "Tollgate Auto Insurance", "Tollgate Standard Driver"),
]


def codes(text: str, line: str | None = None) -> list[str]:
    return sorted(p.code for p in mentioned_products(text, CATALOG, line))


@pytest.mark.parametrize(
    ("text", "line", "expected"),
    [
        ("Is maternity covered on the Solace Gold plan?", None, ["SOLACE-GOLD"]),
        (
            "Does the Tollgate comprehensive policy include a rental car?",
            "auto",
            ["TOLLGATE-COMPREHENSIVE"],
        ),
        ("Kestrel 20: is suicide excluded?", None, ["KESTREL-TERM-20"]),
        # A carrier alone stays ambiguous: all of its products (in the line, when known).
        ("Does the Kestrel policy cover suicide?", None, ["KESTREL-TERM-10", "KESTREL-TERM-20"]),
        ("What does Evergreen exclude?", "life", ["EVERGREEN-TERM-20"]),
        # Common words without a carrier name never pick a product.
        ("I need gold standard family health cover", "health", []),
        ("What's excluded?", None, []),
    ],
)
def test_products_named_in_a_message(text: str, line: str | None, expected: list[str]) -> None:
    assert codes(text, line) == expected


def test_a_line_that_matches_nothing_is_ignored() -> None:
    assert codes("Tell me about Solace", "life") == ["SOLACE-GOLD", "SOLACE-SILVER"]
