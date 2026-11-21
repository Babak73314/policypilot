"""The MCP server through the official SDK client (Streamable HTTP, in-process)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.db
BASE = "http://localhost:8000"


@asynccontextmanager
async def mcp_session(app: Any, token: str | None) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx2.ASGITransport(app=app)
    async with (
        httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers, timeout=30) as http,
        streamable_http_client(f"{BASE}/mcp", http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


def payload(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if structured:
        return structured
    return json.loads(result.content[0].text)


async def test_tools_are_listed_and_quotes_match_the_engine(app: Any, settings: Any) -> None:
    async with mcp_session(app, settings.mcp_dev_token.get_secret_value()) as session:
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert tools >= {
            "search_products",
            "quote_products",
            "get_quote",
            "compare_products",
            "search_policy_documents",
            "get_application_status",
            "create_lead",
        }
        result = await session.call_tool(
            "quote_products",
            {"inputs": {"line": "life", "age": 40, "coverage_minor": 50_000_000}, "term_years": 20},
        )
        quotes = payload(result)["quotes"]
        assert {q["productName"]: q["premium"]["amountMinor"] for q in quotes} == {
            "Evergreen Term Life 20 Flex": 7300,
            "Kestrel Term Life 20": 7500,
        }


async def test_create_lead_needs_the_write_scope(app: Any, prepared_db: Any) -> None:
    from policypilot.services.tokens import create_token

    async with prepared_db.transaction() as session:
        read_only = await create_token(
            session, name="test-read-only", scopes=["mcp:read"], created_by="test"
        )
    lead = {
        "lead": {
            "name": "Ana",
            "email": "ana@example.test",
            "line_interest": ["life"],
            "consent_contact": True,
            "consent_text_version": "test-v1",
        }
    }
    async with mcp_session(app, read_only) as session:
        result = await session.call_tool("create_lead", lead)
        assert result.is_error
        assert "mcp:write" in result.content[0].text


async def test_requests_without_a_token_are_refused(app: Any) -> None:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as http:
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
