"""The eval checks themselves (evals/checks.py): they must fail when they should."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from evals.checks import TurnRecord, check_profile, check_run, check_text, money_amounts
from evals.scenarios import Expect, load_scenarios


def test_money_amounts_in_minor_units() -> None:
    assert money_amounts("From $73.00 per month, or $1,234.5 a year; cover $500k") == {
        7300,
        123450,
        50000000,
    }
    assert money_amounts("no amounts here") == set()


def test_text_expectations_fail_on_mismatch() -> None:
    turn = TurnRecord(text="hi", reply="Here is an estimate. Is that ok? Anything else?")
    assert check_text(Expect(reply_must_include=["estimate"]), turn) == []
    assert check_text(Expect(reply_must_include=["guarantee"]), turn) == ["reply lacks 'guarantee'"]
    assert check_text(Expect(reply_must_not_include=["ESTIMATE"]), turn)  # case-insensitive
    assert check_text(Expect(reply_any_of=["foo", "bar"]), turn)
    assert check_text(Expect(questions_max=1), turn) == ["reply asks 2 questions (max 1)"]


def test_run_expectations_fail_on_mismatch() -> None:
    turn = TurnRecord(text="x", route="quote", tools=["quote_products"], quote_ids=["q1"])
    assert (
        check_run(Expect(route=["quote"], tools_called=["quote_products"], quotes_min=1), turn)
        == []
    )
    problems = check_run(
        Expect(
            route=["compare"], tools_not_called=["quote_products"], quotes_max=0, comparison=True
        ),
        turn,
    )
    assert len(problems) == 4


def test_profile_expectations() -> None:
    profile = {"age": 40, "lines": ["life"], "dependents": None}
    assert check_profile({"age": 40, "lines": ["life"]}, profile) == []
    assert check_profile({"age": 41, "dependents": 2}, profile) == [
        "profile.age = 40 (expected 41)",
        "profile.dependents = None (expected 2)",
    ]


def test_the_suite_has_forty_scenarios_in_six_groups() -> None:
    scenarios = load_scenarios()
    groups = {s.group for s in scenarios}
    assert len(scenarios) == 40
    assert groups == {"needs_analysis", "products", "quotes", "policy_qa", "safety", "handoff"}


def test_a_low_judge_score_fails_the_scenario() -> None:
    from evals.runner import ScenarioResult, apply_judge

    def result() -> ScenarioResult:
        return ScenarioResult(
            id="x", group="policy_qa", description="", passed=True, failures=[], turns=[]
        )

    good = result()
    apply_judge(good, [{"criterion": "Supported", "score": 4, "reason": "ok"}], 3)
    assert good.passed
    bad = result()
    apply_judge(bad, [{"criterion": "Supported", "score": 2, "reason": "invents a clause"}], 3)
    assert not bad.passed
    assert "invents a clause" in bad.failures[0]


def test_the_judge_sees_the_cited_policy_text() -> None:
    from evals.judge import PROMPT

    prompt = PROMPT.format(transcript="t", evidence="4.1 Suicide. We will not pay", criteria="c")
    assert "4.1 Suicide. We will not pay" in prompt


def _result(cost_micros: int) -> Any:
    from evals.runner import ScenarioResult

    turn = TurnRecord(text="hi", cost_micros=cost_micros)
    return ScenarioResult(
        id="x", group="quotes", description="", passed=True, failures=[], turns=[turn]
    )


def test_the_live_run_stops_before_the_cost_cap() -> None:
    from evals.runner import within_budget

    assert within_budget([], 1_000_000)
    assert within_budget([_result(400_000)], 1_000_000)  # 0.4 spent + 0.4 expected <= 1.0
    assert not within_budget([_result(400_000), _result(300_000)], 1_000_000)  # 0.7 + 0.35
    assert within_budget([_result(5_000_000)], None)  # offline runs have no cap


def test_judge_cost_counts_toward_the_total() -> None:
    from evals.judge import usage_cost_micros

    result = _result(1_000)
    result.judge_cost_micros = 500
    assert result.cost_micros == 1_500

    reply = SimpleNamespace(usage_metadata={"input_tokens": 10_000, "output_tokens": 1_000})
    # claude-haiku-5-5: $0.10 / $0.50 per million tokens
    assert usage_cost_micros(reply) == 1_000 + 500
    assert usage_cost_micros(object()) == 0


def test_the_report_counts_guardrail_rejections() -> None:
    from evals.runner import guardrail_summary

    clean = TurnRecord(text="a", guardrail_actions=["disclaimer_added"])
    fallback = TurnRecord(
        text="b",
        guardrail_actions=["template_fallback"],
        guardrail_problems=["template_fallback: unverified_premium:8000"],
    )
    repaired = TurnRecord(
        text="c", guardrail_actions=["repair"], guardrail_problems=["repair: banned_claim:x"]
    )
    summary = guardrail_summary([clean, fallback, repaired, clean])
    assert summary["turns"] == 4
    assert summary["rejectedTurns"] == 2
    assert summary["templateFallbacks"] == 1
    assert summary["repairs"] == 1
    assert summary["templateFallbackRate"] == 0.25
    assert summary["problems"] == [
        "template_fallback: unverified_premium:8000",
        "repair: banned_claim:x",
    ]
