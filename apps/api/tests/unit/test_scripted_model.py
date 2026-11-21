"""The scripted demo model's policy answers (agents/fake_model.py).

A policy question gets the clause sentence that answers it, followed by the citation, and no
"compare, ask, or apply" follow-up line.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from policypilot.agents.fake_model import ScriptedChatModel, policy_answer


def chunk(chunk_id: int, section: str, text: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "document": "Kestrel Term Life 20 - Policy Wording",
        "section": section,
        "text": f"<document id={chunk_id}>{text}</document>",
    }


SUICIDE = chunk(
    12,
    "4.1",
    "4.1 Suicide. We will not pay the Sum Insured if your death is caused by suicide, whether"
    " sane or insane, within 2 years of the Issue Date. In that case we will refund the payments"
    " you have made, less any amount already advanced.",
)
GRACE = chunk(
    7,
    "2.4",
    "2.4 Grace period. If a payment is missed we will keep your cover in force for 31 days after"
    " the due date. If the payment is not made by the end of the grace period, the policy lapses.",
)


def test_the_answer_is_the_matching_sentence_not_the_heading() -> None:
    answer = policy_answer(
        [SUICIDE, GRACE], "Does the Kestrel policy cover suicide in the first years?"
    )
    assert answer == (
        "Kestrel Term Life 20 - Policy Wording, clause 4.1 (Suicide): We will not pay the Sum"
        " Insured if your death is caused by suicide, whether sane or insane, within 2 years of"
        " the Issue Date. [doc:12]"
    )


def test_a_lower_ranked_chunk_wins_when_it_answers_the_question() -> None:
    answer = policy_answer([SUICIDE, GRACE], "How long is the grace period after a missed payment?")
    assert answer is not None
    assert answer.startswith("Kestrel Term Life 20 - Policy Wording, clause 2.4 (Grace period): If")
    assert "31 days" in answer
    assert answer.endswith("[doc:7]")


def test_no_chunks_means_no_answer() -> None:
    assert policy_answer([], "anything") is None


def test_policy_answers_have_no_follow_up_line() -> None:
    model = ScriptedChatModel(role="advisor").bind_tools([])
    messages = [
        SystemMessage(content="Intent: product_question"),
        HumanMessage(content="Does Kestrel cover suicide?"),
        AIMessage(
            content="", tool_calls=[{"name": "search_policy_documents", "args": {}, "id": "c1"}]
        ),
        ToolMessage(
            content=json.dumps({"chunks": [SUICIDE]}),
            name="search_policy_documents",
            tool_call_id="c1",
        ),
    ]
    reply = str(model.invoke(messages, tools=[{"type": "function"}]).content)
    assert reply.endswith("[doc:12]")
    assert "compare" not in reply.lower()


def test_the_customer_is_the_driver_unless_they_name_another() -> None:
    from policypilot.agents.nodes import assumed_driver_age
    from policypilot.domain.profile import CustomerProfile

    assert assumed_driver_age(CustomerProfile(age=30)) == 30
    assert assumed_driver_age(CustomerProfile(age=45, driver_age=19)) is None  # a named driver
    assert assumed_driver_age(CustomerProfile()) is None
