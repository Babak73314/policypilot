"""Runs scenarios through the real API in-process and checks them (testing-and-evals.md).

Offline (default): the scripted fake model and the hashing embedder; it catches pipeline
regressions (routing rules, tool use, the engine's prices, guardrails, redaction, interrupts), not
model quality. Live (`--live`): Claude with real embeddings, the same deterministic checks, plus an
LLM judge for rubric items. The database is reset and seeded first, so point it at a disposable
database: EVAL_DATABASE_URL, or by default the `policypilot_test` database the tests use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from evals.checks import (
    TurnRecord,
    allowed_amounts,
    check_profile,
    check_run,
    check_text,
    citation_products,
    money_amounts,
    quote_product_codes,
    verify_quotes,
)
from evals.scenarios import Scenario

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
REPORT_DIR = API_ROOT / "evals" / "reports"


@dataclass
class ScenarioResult:
    id: str
    group: str
    description: str
    passed: bool
    failures: list[str]
    turns: list[TurnRecord]
    judge: list[dict[str, Any]] = field(default_factory=list)
    judge_cost_micros: int = 0

    @property
    def cost_micros(self) -> int:
        return sum(t.cost_micros for t in self.turns) + self.judge_cost_micros


def _dotenv(name: str) -> str | None:
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return None


def eval_database_url() -> str:
    if url := os.environ.get("EVAL_DATABASE_URL") or os.environ.get("TEST_DATABASE_URL"):
        return url
    password = _dotenv("POLICYPILOT_DB_PASSWORD")
    if not password:
        raise SystemExit(
            "no database: set EVAL_DATABASE_URL, or run scripts/init_env.py and start `db`"
        )
    port = _dotenv("DB_PORT") or "5432"
    return f"postgresql+psycopg://policypilot:{password}@127.0.0.1:{port}/policypilot_test"


def configure_environment(live: bool, documents_dir: Path) -> None:
    """Settings for the eval process; real keys are only read in live mode."""
    url = eval_database_url()
    env = {
        "APP_ENV": "test",
        "LOG_LEVEL": "warning",
        "DATABASE_URL": url,
        "POLICYPILOT_EXPECTED_DB": url.rsplit("/", 1)[-1].split("?")[0],
        "APP_SECRET_KEY": "eval-secret-key-0123456789abcdef",  # gitleaks:allow (eval-only value)
        "LLM_PROVIDER": "anthropic" if live else "fake",
        "EMBEDDING_PROVIDER": "fastembed" if live else "hashing",
        "SEED_AGENT_PASSWORD": "eval-agent-password",
        "WHATSAPP_APP_SECRET": "eval-whatsapp-secret",
        "WHATSAPP_VERIFY_TOKEN": "eval-verify-token",
        "N8N_EVENTS_URL": "",
        "DOCUMENTS_OUTPUT_DIR": str(documents_dir),
    }
    if live:
        key = os.environ.get("ANTHROPIC_API_KEY") or _dotenv("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("--live needs ANTHROPIC_API_KEY (environment or .env)")
        env["ANTHROPIC_API_KEY"] = key
    os.environ.update(env)


async def prepare_database(settings: Any, embedder: Any) -> Any:
    import psycopg
    from alembic import command
    from alembic.config import Config

    from policypilot.agents.checkpoint import open_checkpointer
    from policypilot.db.session import Database
    from policypilot.seed.run import run_seed

    async with await psycopg.AsyncConnection.connect(settings.psycopg_url, autocommit=True) as conn:
        await conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        await conn.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    await asyncio.to_thread(command.upgrade, config, "head")
    async with open_checkpointer(settings) as (saver, _pool):
        await saver.setup()
    db = Database(settings)
    await run_seed(db, settings, embedder)
    return db


@asynccontextmanager
async def running_app(settings: Any, embedder: Any) -> Any:
    """The FastAPI app with its lifespan entered in one dedicated task (as the tests do)."""
    from policypilot.main import create_app

    application = create_app(settings, embedder=embedder)
    started, stop = asyncio.Event(), asyncio.Event()
    failure: list[BaseException] = []

    async def serve() -> None:
        try:
            async with application.router.lifespan_context(application):
                started.set()
                await stop.wait()
        except BaseException as exc:
            failure.append(exc)
            started.set()
            raise

    task = asyncio.create_task(serve())
    await started.wait()
    if failure:
        raise failure[0]
    try:
        yield application
    finally:
        stop.set()
        await task


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data is not None:
            events.append((name, json.loads(data)))
    return events


async def _fill_from_run(state: Any, conversation_id: str, turn: TurnRecord) -> None:
    from policypilot.db.models import AgentRun, AgentRunStep

    async with state.db.session() as session:
        run = (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.conversation_id == uuid.UUID(conversation_id))
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run is None:
            return
        turn.route = run.route
        turn.guardrail_actions = list(run.guardrail_actions or [])
        turn.models = list(run.models or [])
        turn.fallback_used = run.fallback_used
        turn.input_tokens, turn.output_tokens = run.input_tokens, run.output_tokens
        turn.cost_micros = run.cost_estimate_micros
        steps = await session.execute(
            select(AgentRunStep.kind, AgentRunStep.name, AgentRunStep.summary)
            .where(AgentRunStep.run_id == run.id, AgentRunStep.kind.in_(("tool", "guardrail")))
            .order_by(AgentRunStep.seq)
        )
        for kind, name, summary in steps:
            if kind == "tool":
                turn.tools.append(name)
            elif name in ("repair", "template_fallback"):
                turn.guardrail_problems.append(f"{name}: {(summary or {}).get('detail', '')}")


async def run_scenario(client: httpx.AsyncClient, app: Any, scenario: Scenario) -> ScenarioResult:
    from policypilot.api.state import RateLimiter
    from policypilot.db.models import Conversation, Lead, Message, ReviewTask

    state = app.state.policypilot
    state.limiter = RateLimiter()  # 40 sessions from one address would hit the per-IP limit
    created = (await client.post("/api/v1/chat/sessions", json={"locale": "en"})).json()
    conversation_id, token = created["conversationId"], created["token"]
    failures: list[str] = []
    turns: list[TurnRecord] = []
    all_quotes: list[str] = []
    customer_text = ""

    for index, step in enumerate(scenario.turns):
        record = TurnRecord(text=step.user)
        customer_text += "\n" + step.user
        body: dict[str, Any] = {"text": step.user, "clientMessageId": f"eval-{uuid.uuid4()}"}
        if step.action:
            body["action"] = step.action
        started = time.perf_counter()
        response = await client.post(
            f"/api/v1/chat/sessions/{conversation_id}/messages",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=180,
        )
        record.latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            failures.append(f"turn {index + 1}: HTTP {response.status_code} {response.text[:200]}")
            turns.append(record)
            break
        for name, data in parse_sse(response.text):
            if name == "artifact.quote":
                record.quote_ids.append(data["quoteId"])
            elif name == "artifact.comparison":
                record.comparison = True
            elif name == "message.completed":
                record.reply, record.status = data["text"], data["conversationStatus"]
                record.citations = data.get("citations", [])
                token = data.get("token", token)
            elif name == "error":
                record.error = data
        await _fill_from_run(state, conversation_id, record)
        all_quotes.extend(record.quote_ids)
        turns.append(record)

        # Invariants on every turn.
        if record.error or not record.reply:
            failures.append(f"turn {index + 1}: no validated reply ({record.error})")
            break
        async with state.db.session() as session:
            failures += [
                f"turn {index + 1}: {p}" for p in await verify_quotes(session, record.quote_ids)
            ]
            allowed = await allowed_amounts(session, all_quotes, customer_text)
        stray = money_amounts(record.reply) - allowed
        if stray:
            failures.append(
                f"turn {index + 1}: reply has amounts not from the engine or the customer:"
                f" {sorted(stray)}"
            )

    if turns and not failures:
        final, expect = turns[-1], scenario.expect
        failures += check_run(expect, final) + check_text(expect, final)
        async with state.db.session() as session:
            if expect.quote_products:
                codes = await quote_product_codes(session, final.quote_ids)
                missing = set(expect.quote_products) - codes
                if missing:
                    failures.append(f"no quote for {sorted(missing)} (quoted: {sorted(codes)})")
            if expect.citation_products:
                cited = await citation_products(
                    session, [int(c["chunkId"]) for c in final.citations]
                )
                wrong = cited - set(expect.citation_products)
                if wrong or not cited:
                    failures.append(
                        f"citations from {sorted(cited)} (expected {expect.citation_products})"
                    )
            if expect.review_task:
                task = (
                    await session.execute(
                        select(ReviewTask.kind).where(
                            ReviewTask.conversation_id == uuid.UUID(conversation_id)
                        )
                    )
                ).scalar_one_or_none()
                if task != expect.review_task:
                    failures.append(f"review task {task!r} (expected {expect.review_task!r})")
            if expect.lead:
                conversation = await session.get(Conversation, uuid.UUID(conversation_id))
                assert conversation is not None
                kinds = set(
                    (
                        await session.execute(
                            select(Lead.kind).where(Lead.customer_id == conversation.customer_id)
                        )
                    ).scalars()
                )
                if expect.lead not in kinds:
                    failures.append(f"no {expect.lead} lead (leads: {sorted(kinds)})")
            if expect.stored_text_must_not_include:
                stored = (
                    await session.execute(
                        select(Message.content).where(
                            Message.conversation_id == uuid.UUID(conversation_id),
                            Message.role == "customer",
                        )
                    )
                ).scalars()
                text = "\n".join(stored)
                for needle in expect.stored_text_must_not_include:
                    if needle in text:
                        failures.append(f"stored customer text contains {needle!r}")
        if expect.profile:
            thread = await state.graph.aget_state({"configurable": {"thread_id": conversation_id}})
            failures += check_profile(expect.profile, (thread.values or {}).get("profile") or {})

    return ScenarioResult(
        id=scenario.id,
        group=scenario.group,
        description=scenario.description,
        passed=not failures,
        failures=failures,
        turns=turns,
    )


def apply_judge(result: ScenarioResult, scores: list[dict[str, Any]], pass_score: int) -> None:
    """Record the judge's scores; any criterion below `pass_score` fails the scenario."""
    result.judge = scores
    low = [j for j in scores if int(j["score"]) < pass_score]
    if low:  # review 02, finding 22: the judge can fail a run
        result.passed = False
        result.failures += [
            f"judge: {j['criterion']!r} scored {j['score']}: {j['reason']}" for j in low
        ]


def record_judge_error(result: ScenarioResult, exc: BaseException) -> None:
    """A judge call that fails counts against the scenario; the run goes on."""
    result.passed = False
    result.failures.append(f"judge error: {type(exc).__name__}: {str(exc)[:200]}")


async def cited_clauses(app: Any, turn: TurnRecord) -> list[str]:
    """The text of the chunks the reply cited, for the judge."""
    from policypilot.db.models import DocumentChunk, PolicyDocument

    ids = [int(c["chunkId"]) for c in turn.citations]
    if not ids:
        return []
    async with app.state.policypilot.db.session() as session:
        rows = await session.execute(
            select(PolicyDocument.title, DocumentChunk.section_ref, DocumentChunk.content)
            .join(PolicyDocument, PolicyDocument.id == DocumentChunk.document_id)
            .where(DocumentChunk.id.in_(ids))
        )
        return [f"{title}, {section}:\n{content}" for title, section, content in rows]


def within_budget(results: list[ScenarioResult], max_cost_micros: int | None) -> bool:
    """Whether one more scenario fits: spent so far plus the average scenario so far."""
    if max_cost_micros is None or not results:
        return True
    spent = sum(r.cost_micros for r in results)
    return spent + spent // len(results) <= max_cost_micros


async def run_all(
    scenarios: list[Scenario], *, live: bool, judge: bool, max_cost_micros: int | None = None
) -> list[ScenarioResult]:
    import tempfile

    documents = Path(tempfile.mkdtemp(prefix="policypilot-eval-docs-"))
    configure_environment(live, documents)
    from policypilot.config import Settings
    from policypilot.rag.embeddings import HashingEmbedder, build_embedder

    settings = Settings()
    embedder = build_embedder(settings) if live else HashingEmbedder(settings.embedding_dimensions)
    db = await prepare_database(settings, embedder)
    await db.dispose()

    results: list[ScenarioResult] = []
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with running_app(settings, embedder) as app:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
            for index, scenario in enumerate(scenarios):
                if not within_budget(results, max_cost_micros):
                    spent = sum(r.cost_micros for r in results) / 1_000_000
                    print(
                        f"  STOP  cost cap: ${spent:.2f} spent; {len(scenarios) - index} "
                        "scenarios not run",
                        flush=True,
                    )
                    break
                result = await run_scenario(client, app, scenario)
                if judge and scenario.judge and result.turns:
                    from evals.judge import PASS_SCORE, judge_turn

                    evidence = await cited_clauses(app, result.turns[-1])
                    try:
                        scores, cost = await judge_turn(settings, scenario, result.turns, evidence)
                    except Exception as exc:  # the judge must not stop the run
                        record_judge_error(result, exc)
                    else:
                        result.judge_cost_micros = cost
                        apply_judge(result, scores, PASS_SCORE)
                results.append(result)
                mark = "PASS" if result.passed else "FAIL"
                print(f"  {mark}  {scenario.group:<15} {scenario.id}", flush=True)
                for failure in result.failures:
                    print(f"        - {failure}", flush=True)
    return results


def guardrail_summary(turns: list[TurnRecord]) -> dict[str, Any]:
    """How often the output guardrail rejected a draft: repaired, or replaced by a template."""
    rejected = [t for t in turns if t.guardrail_problems]
    fallback = sum("template_fallback" in t.guardrail_actions for t in turns)
    return {
        "turns": len(turns),
        "rejectedTurns": len(rejected),
        "repairs": sum("repair" in t.guardrail_actions for t in turns),
        "templateFallbacks": fallback,
        "templateFallbackRate": round(fallback / len(turns), 4) if turns else 0.0,
        "problems": [p for t in rejected for p in t.guardrail_problems],
    }


def write_report(
    results: list[ScenarioResult], *, live: bool, directory: Path = REPORT_DIR
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    turns = [t for r in results for t in r.turns]
    latencies = sorted(t.latency_ms for t in turns) or [0.0]
    p95 = latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
    groups: dict[str, list[ScenarioResult]] = {}
    for result in results:
        groups.setdefault(result.group, []).append(result)
    summary: dict[str, Any] = {
        "mode": "live" if live else "offline",
        "createdAt": stamp,
        "scenarios": len(results),
        "passed": sum(r.passed for r in results),
        "byGroup": {
            g: {"passed": sum(r.passed for r in rs), "total": len(rs)} for g, rs in groups.items()
        },
        "latencyMs": {"p50": round(statistics.median(latencies)), "p95": round(p95)},
        "tokens": {
            "input": sum(t.input_tokens for t in turns),
            "output": sum(t.output_tokens for t in turns),
        },
        "estimatedCostUsd": round(sum(r.cost_micros for r in results) / 1_000_000, 4),
        "judgeCostUsd": round(sum(r.judge_cost_micros for r in results) / 1_000_000, 4),
        "fallbackTurns": sum(t.fallback_used for t in turns),
        "guardrail": guardrail_summary(turns),
    }
    payload = {"summary": summary, "results": [asdict(r) for r in results]}
    json_path = directory / f"{stamp}-{summary['mode']}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Eval report ({summary['mode']}, {stamp})",
        "",
        f"**{summary['passed']} / {summary['scenarios']} scenarios passed.** "
        f"Turn latency p50 {summary['latencyMs']['p50']} ms, p95 {summary['latencyMs']['p95']} ms. "
        f"Tokens {summary['tokens']['input']} in / {summary['tokens']['output']} out, "
        f"estimated cost ${summary['estimatedCostUsd']} "
        f"(judge ${summary['judgeCostUsd']}). "
        f"Guardrail: {summary['guardrail']['templateFallbacks']} of "
        f"{summary['guardrail']['turns']} turns fell back to a template, "
        f"{summary['guardrail']['repairs']} repaired.",
        "",
        "| Group | Passed |",
        "|---|---|",
        *[f"| {g} | {v['passed']} / {v['total']} |" for g, v in summary["byGroup"].items()],
        "",
    ]
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["## Failures", ""]
        for result in failed:
            lines.append(f"- `{result.id}`: " + "; ".join(result.failures))
        lines.append("")
    if summary["guardrail"]["problems"]:
        lines += ["## Guardrail rejections", ""]
        lines += [f"- {problem}" for problem in summary["guardrail"]["problems"]]
        lines.append("")
    judged = [r for r in results if r.judge]
    if judged:
        lines += [
            "## Judge scores (1-5)",
            "",
            "| Scenario | Criterion | Score | Reason |",
            "|---|---|---|---|",
        ]
        for result in judged:
            for item in result.judge:
                reason = str(item.get("reason", "")).replace("|", "/")
                lines.append(f"| {result.id} | {item['criterion']} | {item['score']} | {reason} |")
        lines.append("")
    md_path = json_path.with_suffix(".md")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    from evals.scenarios import load_scenarios

    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--live", action="store_true", help="use Claude and real embeddings (costs money)"
    )
    parser.add_argument("--no-judge", action="store_true", help="live mode without the LLM judge")
    parser.add_argument(
        "--only",
        help="run scenarios whose id contains, or whose group equals, one of these comma-separated"
        " texts (for example the failed ids of a previous run)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=10.0,
        help="live mode: stop before a scenario that would take the estimated cost past this (USD)",
    )
    args = parser.parse_args(argv)

    scenarios = load_scenarios()
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        scenarios = [s for s in scenarios if any(w in s.id or w == s.group for w in wanted)]
    print(
        f"running {len(scenarios)} scenarios ({'live' if args.live else 'offline, scripted model'})"
    )

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    budget = round(args.max_cost * 1_000_000) if args.live else None
    results = asyncio.run(
        run_all(
            scenarios, live=args.live, judge=args.live and not args.no_judge, max_cost_micros=budget
        )
    )
    report = write_report(results, live=args.live)
    passed = sum(r.passed for r in results)
    print(f"{passed} / {len(results)} passed; report: {report.relative_to(API_ROOT)}")
    return 0 if passed == len(results) else 1
