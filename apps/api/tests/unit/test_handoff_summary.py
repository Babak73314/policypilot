"""The handoff summary keeps only the model's text (found in the first live run).

Claude's reply is a list of content blocks, including thinking blocks with signatures. The
handoff stored `str(content)`, so the agent desk showed the raw blocks instead of the summary.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from policypilot.agents.nodes import message_text
from policypilot.agents.review_nodes import handoff

CLAUDE_REPLY = AIMessage(
    content=[
        {"type": "thinking", "thinking": "Now I'm writing a short summary", "signature": "CAQSrw"},
        {"type": "text", "text": "Customer, 38, two children, wants family health cover."},
    ]
)


def test_message_text_drops_thinking_blocks() -> None:
    assert message_text(CLAUDE_REPLY) == "Customer, 38, two children, wants family health cover."
    assert message_text(AIMessage(content="  plain text ")) == "plain text"


async def test_the_handoff_summary_has_no_thinking_blocks() -> None:
    class Writer:
        async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
            return CLAUDE_REPLY

    context = SimpleNamespace(
        models=SimpleNamespace(plain=lambda role: Writer()), llm_config=lambda: {}
    )
    state: Any = {"messages": [HumanMessage(content="Can I talk to a person?")]}
    result = await handoff(state, SimpleNamespace(context=context))  # type: ignore[arg-type]
    summary = result["review_request"]["summary"]
    assert summary == "Customer, 38, two children, wants family health cover."
    assert "signature" not in summary
    assert "thinking" not in summary
