"""Model roles, provider fallback and the daily model-call budget (ADR-0006, ADR-0007, ADR-0017).

- Each role (router, extractor, advisor, writer) is configured separately.
- Structured output always uses ``method="json_schema"`` (native ``output_config.format``);
  tools are bound with ``strict=True`` and automatic tool choice, never forced.
- The provider fallback (OpenAI) handles transport, rate-limit and server errors only.
- Every model call first reserves one unit of the daily budget, atomically, in Postgres.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import anthropic
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel
from sqlalchemy import text

from policypilot.config import ModelRole, Settings
from policypilot.db.session import Database

SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
SERVER_SIDE_FALLBACK_MODELS = frozenset(
    {
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-fable-5-1",
        "claude-fable-5-2",
    }
)
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,  # includes APITimeoutError
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.GatewayTimeoutError,
)
ROLE_LIMITS: dict[ModelRole, tuple[int, float]] = {  # (max output tokens, timeout seconds)
    "router": (512, 15.0),
    "extractor": (512, 15.0),
    "advisor": (2048, 45.0),
    "writer": (800, 30.0),
}


class ModelBudgetExceededError(RuntimeError):
    """The daily model-call budget is used up (security-and-privacy.md)."""


# Everything a model call can fail with that has a deterministic way out (a template, a fallback
# summary, or skipping an optional step).
MODEL_FAILURES: tuple[type[BaseException], ...] = (
    ModelBudgetExceededError,
    TimeoutError,
    ValueError,
    *TRANSIENT_ERRORS,
)


_RESERVE_SQL = text(
    """
    INSERT INTO app.llm_usage_counters (day, calls, limit_value)
    VALUES (current_date, 1, :limit)
    ON CONFLICT (day) DO UPDATE
      SET calls = app.llm_usage_counters.calls + 1
      WHERE app.llm_usage_counters.calls < app.llm_usage_counters.limit_value
    RETURNING calls
    """
)


async def reserve_model_call(db: Database, limit: int) -> bool:
    async with db.transaction() as session:
        row = (await session.execute(_RESERVE_SQL, {"limit": limit})).first()
    return row is not None


class BudgetCallback(AsyncCallbackHandler):
    """Reserves one budget unit when any chat model call starts; raises when exhausted."""

    raise_error = True

    def __init__(self, reserve: Callable[[], Awaitable[bool]]) -> None:
        self._reserve = reserve

    async def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        if not await self._reserve():
            raise ModelBudgetExceededError("daily model-call budget exhausted")


class ModelRoles:
    """Builds runnables per role: Claude (or the scripted fake) with an optional fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = settings.resolved_llm_provider

    # ------------------------------------------------------------------ construction

    def _claude(self, role: ModelRole) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        model = self.settings.model_for(role)
        max_tokens, timeout = ROLE_LIMITS[role]
        kwargs: dict[str, Any] = {}
        effort = {
            "router": "low",
            "extractor": "low",
            "advisor": self.settings.llm_advisor_effort,
            "writer": self.settings.llm_writer_effort,
        }[role]
        betas: list[str] = []
        if self.settings.llm_server_side_fallback and model in SERVER_SIDE_FALLBACK_MODELS:
            betas.append(SERVER_SIDE_FALLBACK_BETA)
            kwargs["model_kwargs"] = {"fallbacks": "default"}
        return ChatAnthropic(
            model=model,
            api_key=self.settings.anthropic_api_key,
            max_tokens=max_tokens,
            max_retries=1,
            default_request_timeout=timeout,
            reasoning_effort=effort,
            betas=betas or None,
            **kwargs,
        )

    def _openai(self, role: ModelRole) -> BaseChatModel | None:
        if self.settings.llm_fallback_provider != "openai":
            return None
        key = self.settings.openai_api_key.get_secret_value()
        if not key:
            return None
        from langchain_openai import ChatOpenAI

        small = role in ("router", "extractor")
        max_tokens, timeout = ROLE_LIMITS[role]
        return ChatOpenAI(
            model=(
                self.settings.llm_fallback_small_model
                if small
                else self.settings.llm_fallback_large_model
            ),
            api_key=self.settings.openai_api_key,
            use_responses_api=True,
            max_retries=1,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    def _fake(self, role: ModelRole) -> BaseChatModel:
        from policypilot.agents.fake_model import ScriptedChatModel

        return ScriptedChatModel(role=role)

    def base(self, role: ModelRole) -> tuple[BaseChatModel, BaseChatModel | None]:
        if self.provider == "fake":
            return self._fake(role), None
        return self._claude(role), self._openai(role)

    def model_name(self, role: ModelRole) -> str:
        return "scripted-fake" if self.provider == "fake" else self.settings.model_for(role)

    # ------------------------------------------------------------------ runnables

    def structured(self, role: ModelRole, schema: type[BaseModel]) -> Runnable[Any, Any]:
        primary, fallback = self.base(role)
        runnable: Runnable[Any, Any] = primary.with_structured_output(schema, method="json_schema")
        if fallback is not None:
            runnable = runnable.with_fallbacks(
                [fallback.with_structured_output(schema, method="json_schema")],
                exceptions_to_handle=TRANSIENT_ERRORS,
            )
        return runnable

    def with_tools(self, role: ModelRole, tools: Sequence[BaseTool]) -> Runnable[Any, Any]:
        primary, fallback = self.base(role)
        runnable: Runnable[Any, Any] = primary.bind_tools(list(tools), strict=True)
        if fallback is not None:
            runnable = runnable.with_fallbacks(
                [fallback.bind_tools(list(tools), strict=True)],
                exceptions_to_handle=TRANSIENT_ERRORS,
            )
        return runnable

    def plain(self, role: ModelRole) -> Runnable[Any, Any]:
        primary, fallback = self.base(role)
        if fallback is None:
            return primary
        return primary.with_fallbacks([fallback], exceptions_to_handle=TRANSIENT_ERRORS)
