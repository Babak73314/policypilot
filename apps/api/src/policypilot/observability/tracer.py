"""Built-in run traces (ADR-0011): one agent_runs row and ordered steps per graph invocation.

Steps are buffered in memory and written in one transaction at the end of the run.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from policypilot.db.models import AgentRun, AgentRunStep
from policypilot.db.session import Database
from policypilot.observability.pricing import estimate_cost_micros
from policypilot.security.pii import mask_pii


def _summary_text(value: Any, limit: int = 200) -> str:
    return mask_pii(str(value))[:limit]


@dataclass
class _Step:
    seq: int
    kind: str
    name: str
    started_at: datetime
    duration_ms: int
    model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)


class RunTrace:
    def __init__(self, conversation_id: uuid.UUID, channel: str, turn_id: uuid.UUID | None) -> None:
        self.id = uuid.uuid4()
        self.conversation_id = conversation_id
        self.channel = channel
        self.turn_id = turn_id
        self.started_at = datetime.now(UTC)
        self.steps: list[_Step] = []
        self.models: set[str] = set()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cost_micros = 0
        self.fallback_used = False
        self.guardrail_actions: list[str] = []
        self.route: str | None = None

    def step(self, kind: str, name: str, started: float, **extra: Any) -> None:
        self.steps.append(
            _Step(
                seq=len(self.steps) + 1,
                kind=kind,
                name=name,
                started_at=datetime.now(UTC),
                duration_ms=int((time.monotonic() - started) * 1000),
                **extra,
            )
        )

    def guardrail(self, action: str, detail: str = "") -> None:
        self.guardrail_actions.append(action)
        summary = {"detail": _summary_text(detail, limit=600)}  # room for the evidence
        self.step("guardrail", action, time.monotonic(), summary=summary)

    async def persist(self, db: Database, status: str, error: str | None = None) -> None:
        async with db.transaction() as session:
            session.add(
                AgentRun(
                    id=self.id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                    channel=self.channel,
                    route=self.route,
                    started_at=self.started_at,
                    finished_at=datetime.now(UTC),
                    status=status,
                    models=sorted(self.models),
                    fallback_used=self.fallback_used,
                    input_tokens=self.input_tokens,
                    output_tokens=self.output_tokens,
                    cache_read_tokens=self.cache_read_tokens,
                    cost_estimate_micros=self.cost_micros,
                    guardrail_actions=self.guardrail_actions,
                    error=_summary_text(error) if error else None,
                )
            )
            await session.flush()
            for step in self.steps:
                session.add(
                    AgentRunStep(
                        run_id=self.id,
                        seq=step.seq,
                        kind=step.kind,
                        name=step.name,
                        started_at=step.started_at,
                        duration_ms=step.duration_ms,
                        model=step.model,
                        provider=step.provider,
                        input_tokens=step.input_tokens,
                        output_tokens=step.output_tokens,
                        summary=step.summary,
                    )
                )


class TraceCallback(AsyncCallbackHandler):
    """Records every chat-model call (model, provider, tokens, latency) into the RunTrace."""

    def __init__(self, trace: RunTrace, primary_provider: str) -> None:
        self.trace = trace
        self.primary_provider = primary_provider
        self._starts: dict[uuid.UUID, tuple[float, str, str]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        params = invocation_params or {}
        model = str(params.get("model") or params.get("model_name") or "unknown")
        node = str((metadata or {}).get("langgraph_node", ""))
        self._starts[run_id] = (time.monotonic(), model, node)

    async def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        started, model, node = self._starts.pop(run_id, (time.monotonic(), "unknown", ""))
        usage: dict[str, Any] = {}
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = dict(getattr(message, "usage_metadata", None) or {}) or usage
                meta = getattr(message, "response_metadata", None) or {}
                model = str(meta.get("model_name") or meta.get("model") or model)
        provider = (
            "fake"
            if model == "scripted-fake"
            else "openai"
            if model.startswith("gpt")
            else "anthropic"
        )
        if provider != self.primary_provider:
            self.trace.fallback_used = True
        in_tokens = int(usage.get("input_tokens", 0))
        out_tokens = int(usage.get("output_tokens", 0))
        cache_read = int((usage.get("input_token_details") or {}).get("cache_read", 0))
        self.trace.models.add(model)
        self.trace.input_tokens += in_tokens
        self.trace.output_tokens += out_tokens
        self.trace.cache_read_tokens += cache_read
        self.trace.cost_micros += estimate_cost_micros(model, in_tokens, out_tokens, cache_read)
        self.trace.step(
            "llm",
            node or "model",
            started,
            model=model,
            provider=provider,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )

    async def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        started, model, node = self._starts.pop(run_id, (time.monotonic(), "unknown", ""))
        self.trace.step(
            "llm",
            node or "model",
            started,
            model=model,
            summary={"error": type(error).__name__},
        )
