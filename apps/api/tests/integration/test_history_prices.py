"""Rendered prices stay out of the model's own history (found in the live demo recording).

Claude saw the smoker prices the system had rendered into its earlier reply, took them for
prices it had typed, and told the customer to ignore them. The customer still sees the prices;
the history the model reads has a marker instead.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from tests.helpers import completed, new_session, say

from policypilot.agents.guardrails import QUOTED_PRICE


async def test_the_model_never_reads_rendered_prices_in_its_own_replies(
    client: Any, app: Any
) -> None:
    conversation_id, headers = await new_session(client)
    _, events = await say(
        client, conversation_id, headers, "I'm 40 and want $500,000 of term life cover for 20 years"
    )
    shown = completed(events)["text"]
    assert "per month" in shown  # the customer sees the rendered prices
    assert "$" in shown

    thread = await app.state.policypilot.graph.aget_state(
        {"configurable": {"thread_id": conversation_id}}
    )
    replies = [m for m in thread.values["messages"] if isinstance(m, AIMessage) and m.content]
    remembered = str(replies[-1].content)
    assert QUOTED_PRICE in remembered
    assert "per month" not in remembered
    assert "$" not in remembered
