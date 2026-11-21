"""MCP smoke client: the official Python SDK against the running server (Streamable HTTP).

    cd apps/api && uv run python ../../scripts/mcp_smoke.py      # or: make mcp-smoke

It reads MCP_DEV_TOKEN from the repository-root `.env` (or the environment), lists the tools,
quotes term life for a 40-year-old and checks that a request without a token is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "search_products",
    "quote_products",
    "get_quote",
    "compare_products",
    "search_policy_documents",
    "get_application_status",
    "create_lead",
}


def read_token() -> str:
    if token := os.environ.get("MCP_DEV_TOKEN"):
        return token
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("MCP_DEV_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("MCP_DEV_TOKEN not set and not found in .env")


def payload(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    return structured or json.loads(result.content[0].text)


async def run(url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        info = await session.initialize()
        print(f"  ok  connected to {info.server_info.name} {info.server_info.version}")
        tools = {tool.name for tool in (await session.list_tools()).tools}
        missing = EXPECTED_TOOLS - tools
        assert not missing, f"missing tools: {missing}"
        print(f"  ok  {len(tools)} tools listed")
        result = await session.call_tool(
            "quote_products",
            {"inputs": {"line": "life", "age": 40, "coverage_minor": 50_000_000}, "term_years": 20},
        )
        assert not result.is_error, result.content
        quotes = payload(result)["quotes"]
        assert quotes, "no quotes returned"
        for quote in quotes:
            premium = quote["premium"]
            print(
                f"  ok  {quote['productName']}: {premium['amountMinor'] / 100:.2f} "
                f"{premium['currency']} ({quote['billingPeriod']})"
            )
    async with httpx2.AsyncClient(timeout=10) as http:
        response = await http.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401, f"unauthenticated request answered {response.status_code}"
    print("  ok  a request without a token is refused (401)")


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP smoke test")
    parser.add_argument("--url", default="http://127.0.0.1:8080/mcp")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, read_token()))
    except (AssertionError, httpx2.HTTPError, OSError) as error:
        print(f"FAIL {error!r}", file=sys.stderr)
        return 1
    print("mcp smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
