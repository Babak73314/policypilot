"""Runtime context passed to every node (LangGraph `context_schema`). Never checkpointed."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from policypilot.agents.models import BudgetCallback, ModelRoles, reserve_model_call
from policypilot.config import Settings
from policypilot.db.session import Database
from policypilot.observability.tracer import RunTrace, TraceCallback
from policypilot.rag.embeddings import Embedder
from policypilot.services.templates import TemplateStore


@dataclass
class AgentContext:
    db: Database
    settings: Settings
    models: ModelRoles
    embedder: Embedder
    templates: TemplateStore
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    channel: str
    locale: str
    turn_id: uuid.UUID
    trace: RunTrace
    redactions: dict[str, int] = field(default_factory=dict)
    stored_text: str = ""

    def llm_config(self) -> RunnableConfig:
        """Callbacks for every model call: budget reservation first, then tracing."""

        async def reserve() -> bool:
            if self.models.provider == "fake":
                return True
            return await reserve_model_call(self.db, self.settings.llm_daily_call_limit)

        primary = "fake" if self.models.provider == "fake" else "anthropic"
        callbacks: list[Any] = [BudgetCallback(reserve), TraceCallback(self.trace, primary)]
        return {"callbacks": callbacks}
