"""The live LLM judge: rubric items scored 1-5 with reasons (claude-haiku-5-5, json_schema output).

It is never the only check: prices, citations and redaction are always verified deterministically
(checks.py). The judge sees the conversation as text and nothing else.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from evals.checks import TurnRecord
from evals.scenarios import Scenario

JUDGE_MODEL = "claude-haiku-5-5"
PASS_SCORE = 3  # a criterion scored below this fails the scenario
REASON_LIMIT = 300


class Score(BaseModel):
    criterion: str
    score: int = Field(ge=1, le=5)
    # No length limit in the schema: a long reason once failed parsing and stopped a whole live
    # run. It is shortened for the report instead.
    reason: str

    @field_validator("reason")
    @classmethod
    def _short(cls, value: str) -> str:
        return value if len(value) <= REASON_LIMIT else value[: REASON_LIMIT - 1].rstrip() + "…"


class Verdict(BaseModel):
    scores: list[Score]


PROMPT = """You grade replies of an insurance agency's AI assistant (a fictional demo agency).
Score each criterion from 1 (clearly fails) to 5 (fully meets it), judging only the final
assistant reply in the light of the conversation. Give one short reason per criterion.

Conversation before the final reply:
{transcript}

Final assistant reply (the one to grade):
{final}

Policy text the final reply cited (judge factual claims about policies only against this; a claim
it does not support counts against the reply):
{evidence}

Criteria:
{criteria}"""


def judge_transcript(turns: list[TurnRecord]) -> tuple[str, str]:
    """(the conversation up to the last customer message, the final reply).

    The final reply is shown on its own: with one turn, the judge once took the only assistant
    line for an earlier message and scored a correct reply as missing.
    """
    lines = [f"Customer: {t.text}\nAssistant: {t.reply}" for t in turns[:-1]]
    lines.append(f"Customer: {turns[-1].text}")
    return "\n".join(lines), turns[-1].reply


async def judge_turn(
    settings: Any, scenario: Scenario, turns: list[TurnRecord], evidence: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """(scores, estimated cost of the call in micro-dollars)."""
    from langchain_anthropic import ChatAnthropic

    transcript, final = judge_transcript(turns)
    criteria = "\n".join(f"- {item}" for item in scenario.judge)
    model = ChatAnthropic(  # same construction as agents/models.py
        model=JUDGE_MODEL,
        api_key=settings.anthropic_api_key,
        max_tokens=1024,
        max_retries=1,
        default_request_timeout=60,
        reasoning_effort="low",
    ).with_structured_output(Verdict, method="json_schema", include_raw=True)
    cited = "\n\n".join(evidence) or "(none)"
    output = await model.ainvoke(
        PROMPT.format(transcript=transcript, final=final, evidence=cited, criteria=criteria)
    )
    assert isinstance(output, dict)  # include_raw=True: {"raw", "parsed", "parsing_error"}
    verdict = output["parsed"]
    assert isinstance(verdict, Verdict), output.get("parsing_error")
    return [s.model_dump() for s in verdict.scores], usage_cost_micros(output["raw"])


def usage_cost_micros(message: Any) -> int:
    """The estimated cost of one judge call, from the response's usage metadata."""
    from policypilot.observability.pricing import estimate_cost_micros

    usage = getattr(message, "usage_metadata", None) or {}
    cache_read = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    return estimate_cost_micros(
        JUDGE_MODEL, usage.get("input_tokens", 0), usage.get("output_tokens", 0), cache_read
    )
