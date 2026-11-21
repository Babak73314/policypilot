"""Health and the public catalog."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from policypilot.api.state import app_state
from policypilot.services.catalog import list_products

router = APIRouter()


@router.get("/healthz", tags=["health"])
async def healthz() -> dict[str, str]:
    """Process liveness only. Never touches the database, so a shared cluster can pause."""
    return {"status": "ok"}


@router.get("/readyz", tags=["health"], response_model=None)
async def readyz(request: Request) -> dict[str, str] | JSONResponse:
    state = app_state(request)
    try:
        async with state.db.session() as session:
            await session.execute(text("SELECT 1"))
            head = (
                await session.execute(text("SELECT version_num FROM app.alembic_version"))
            ).scalar_one()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready", "schema": str(head), "embedder": state.embedder.model_name}


@router.get("/api/v1/catalog/products", tags=["catalog"])
async def public_catalog(
    request: Request,
    line: Annotated[str | None, Query(pattern="^(life|health|auto)$")] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    state = app_state(request)
    locale = (accept_language or "en").split(",")[0].split(";")[0].strip()[:5] or "en"
    async with state.db.session() as session:
        products = await list_products(session, line=line, locale=locale)
    return {
        "items": [
            {
                "id": str(p.id),
                "code": p.code,
                "line": p.line,
                "carrier": {"code": p.carrier_code, "name": p.carrier_name},
                "name": p.name,
                "summary": p.summary,
                "highlights": p.highlights,
                "attributes": p.attributes,
                "currency": p.currency,
                "billingPeriod": p.billing_period,
            }
            for p in products
        ]
    }
