# ADR-0010: MCP server with the official SDK, mounted in the API

- Status: Accepted
- Date: 2026-11-20

## Context

An MCP server lets agents in MCP clients (Claude Desktop, Claude Code, n8n's MCP Client
Tool node) search products, compare them, quote and create leads with the same rules as
the assistant.

The official Python SDK (`mcp`) is at 2.3.0. In it, `FastMCP` is renamed to `MCPServer`,
the 2026-07-28 protocol revision is stateless, and Streamable HTTP is the standard remote
transport. The standalone `fastmcp` package (4.x) adds conveniences but is a third-party
dependency.

## Decision

- Use the **official `mcp` SDK** (`from mcp.server import MCPServer`). The server is mounted in the
  FastAPI app at `/mcp` with `streamable_http_app(stateless_http=True, json_response=True)`.
  FastAPI's lifespan enters `mcp.session_manager.run()`.
- The MCP tools are thin wrappers over **the same service layer** the LangGraph tools use, so there
  is one set of rules:
  - read-only: `search_products`, `compare_products`, `get_quote`, `get_application_status`,
    `search_policy_documents`;
  - write: `create_lead`.
  - It also exposes the resource `catalog://products` and the prompt `needs-analysis`.
- **Auth:** the SDK's resource-server hooks (`TokenVerifier` plus `AuthSettings`) with a custom
  verifier. The verifier checks opaque API tokens that are stored hashed in `app.api_tokens`, with
  scopes `mcp:read` and `mcp:write`. Write tools check for `mcp:write`. Full OAuth with an
  authorization server is Phase 2.
- `TransportSecuritySettings` restricts `allowed_hosts` and `allowed_origins` in production.
- The MCP server never touches conversations or PII beyond what `create_lead` writes. It is a tool
  surface for agency staff and their agents, not for customers.

## Consequences

- One process and one deployment unit, with the auth and service layer shared with the REST API.
- A `scripts/mcp_smoke.py` client and integration tests call the server through the SDK's client.
- If MCP load ever needs to scale apart from chat, it can be split into its own container without
  code changes, because the server is built by a factory.

## Alternatives considered

- **The standalone `fastmcp` package:** convenient, but the brief prefers the official SDK.
- **A separate MCP container:** more moving parts in Phase 1.
