"""FastAPI application factory and lifespan."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from policypilot import __version__
from policypilot.agents.checkpoint import open_checkpointer
from policypilot.agents.graph import build_graph
from policypilot.agents.models import ModelRoles
from policypilot.agents.runner import TurnRunner
from policypilot.api import agent, chat, integrations, public, whatsapp
from policypilot.api.state import AppState
from policypilot.channels.whatsapp.service import WhatsAppChannel
from policypilot.config import Settings, get_settings
from policypilot.db.session import Database
from policypilot.events.dispatcher import OutboxDispatcher
from policypilot.mcp_server.server import build_mcp_server, transport_security
from policypilot.observability.logging import configure_logging, request_context_middleware
from policypilot.rag.embeddings import Embedder, build_embedder
from policypilot.services.events import on_event_committed
from policypilot.services.templates import TemplateStore


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.require_real_secrets()
    configure_logging(settings.log_level)

    db = Database(settings)
    mcp = build_mcp_server(settings, db, embedder_holder := _EmbedderProxy())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_embedder: Embedder = (
            embedder if embedder is not None else await asyncio.to_thread(build_embedder, settings)
        )
        embedder_holder.target = active_embedder
        templates = TemplateStore()
        async with db.session() as session:
            await templates.load(session)
        async with open_checkpointer(settings) as (saver, _pool), mcp.session_manager.run():
            graph = build_graph(saver)
            models = ModelRoles(settings)
            app.state.policypilot = AppState(
                settings=settings,
                db=db,
                graph=graph,
                models=models,
                embedder=active_embedder,
                templates=templates,
                runner=TurnRunner(
                    db=db,
                    settings=settings,
                    graph=graph,
                    models=models,
                    embedder=active_embedder,
                    templates=templates,
                ),
            )
            state: AppState = app.state.policypilot
            state.whatsapp = WhatsAppChannel(
                db=db, settings=settings, runner=state.runner, templates=templates
            )
            state.runner.deliver = state.whatsapp.deliver
            state.dispatcher = OutboxDispatcher(db, settings)
            on_event_committed(state.dispatcher.nudge)
            state.dispatcher.start()
            await state.whatsapp.recover()
            try:
                yield
            finally:
                pending = list(state.background)
                if pending:
                    await asyncio.wait(pending, timeout=30)
                await state.whatsapp.close()
                await state.dispatcher.close()
                await db.dispose()

    app = FastAPI(
        title="PolicyPilot API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Conversation-Token", "X-Request-Id"],
    )
    app.middleware("http")(request_context_middleware)

    @app.exception_handler(HTTPException)
    async def problem_details(request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        body = {
            "type": "about:blank",
            "status": exc.status_code,
            "title": detail.get("message", "Error") if isinstance(detail, dict) else str(detail),
            "code": detail.get("code", "error") if isinstance(detail, dict) else "error",
        }
        return JSONResponse(
            body,
            status_code=exc.status_code,
            headers=exc.headers,
            media_type="application/problem+json",
        )

    app.include_router(public.router)
    app.include_router(chat.router)
    app.include_router(agent.router)
    app.include_router(integrations.router)
    app.include_router(whatsapp.webhook_router)
    if settings.app_env in ("local", "test") and settings.whatsapp_mode == "simulator":
        app.include_router(whatsapp.dev_router)  # never mounted in cloud mode or production
    # Last: the MCP Streamable HTTP app answers /mcp (and its OAuth metadata) under the root.
    app.mount(
        "/",
        mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=transport_security(settings),
        ),
    )
    return app


class _EmbedderProxy:
    """Lets the MCP server be built before the embedder loads in the lifespan."""

    target: Embedder | None = None

    @property
    def model_name(self) -> str:
        return self._target.model_name

    @property
    def dimensions(self) -> int:
        return self._target.dimensions

    @property
    def _target(self) -> Embedder:
        if self.target is None:
            raise RuntimeError("embedder not loaded yet")
        return self.target

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._target.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        return await self._target.embed_query(text)


def app_factory() -> FastAPI:
    """`uvicorn --factory policypilot.main:app_factory`."""
    return create_app()
