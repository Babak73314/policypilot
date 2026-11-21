"""Where number accounting gets its facts (agents/nodes._number_facts), from the first live run.

Claude's replies were rejected for numbers that were true: the customer's own mileage, and the
age limits of the plans it had just checked. Both are now facts; prices never are.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from policypilot.agents.nodes import _number_facts
from policypilot.db.models import Product
from policypilot.domain.profile import CustomerProfile


async def test_customer_numbers_and_eligibility_limits_are_facts(prepared_db: Any) -> None:
    async with prepared_db.session() as session:
        silver = (
            await session.execute(select(Product.id).where(Product.code == "SOLACE-SILVER"))
        ).scalar_one()
        state: dict[str, Any] = {
            "messages": [
                HumanMessage(content="I drive about 12,000 miles a year"),
                AIMessage(content="The deductible is 4,321."),  # the assistant's own words: no
            ],
            "turn_product_ids": [str(silver)],
        }
        money, plain = await _number_facts(session, state, CustomerProfile(), {}, "en")  # type: ignore[arg-type]

    assert Decimal(12000) in plain  # the customer said it
    assert Decimal(4321) not in plain  # an earlier assistant message is not a source
    assert {Decimal(18), Decimal(64)} <= plain  # SOLACE-SILVER accepts ages 18 to 64
    assert 300000 in money  # its $3,000 deductible attribute
    # Rate-table price components (base premiums, per-dependent amounts) never become facts.
    assert Decimal(190) not in plain
    assert 19000 not in money
