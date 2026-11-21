"""Process-wide application state, created in the FastAPI lifespan."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request
from langgraph.graph.state import CompiledStateGraph

from policypilot.agents.models import ModelRoles
from policypilot.agents.runner import TurnRunner
from policypilot.config import Settings
from policypilot.db.session import Database
from policypilot.rag.embeddings import Embedder
from policypilot.services.templates import TemplateStore


class RateLimiter:
    """In-memory sliding-window limiter, per process (Phase 1; docs/design/api.md)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: float) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry = max(1, int(window_seconds - (now - hits[0])))
            raise HTTPException(
                status_code=429,
                detail={"code": "rate_limited", "message": "Too many requests."},
                headers={"Retry-After": str(retry)},
            )
        hits.append(now)


@dataclass
class AppState:
    settings: Settings
    db: Database
    graph: CompiledStateGraph[Any, Any, Any, Any]
    models: ModelRoles
    embedder: Embedder
    templates: TemplateStore
    runner: TurnRunner
    whatsapp: Any = None
    dispatcher: Any = None
    limiter: RateLimiter = field(default_factory=RateLimiter)
    background: set[Any] = field(default_factory=set)


def app_state(request: Request) -> AppState:
    state: AppState = request.app.state.policypilot
    return state


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
