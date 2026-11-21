# MCP server

The MCP server exposes the agency's catalog, comparison, quoting and lead capture to
MCP clients. Typical clients are Claude Desktop, Claude Code, an n8n "MCP Client Tool"
node, or another company's agent. The decision is ADR-0010.

## Runtime

| Item | Value |
|---|---|
| SDK | `mcp` 2.3 (official). `from mcp.server import MCPServer` |
| Transport | Streamable HTTP at `/mcp`, mounted in the FastAPI app. Created with `streamable_http_app(stateless_http=True, json_response=True)`. FastAPI's lifespan enters `mcp.session_manager.run()`. |
| Protocol revisions | 2026-07-28 (stateless) and 2025-11-25, both served by the SDK |
| Auth | OAuth 2.0 resource-server mode in the SDK, with a custom `TokenVerifier` that checks opaque `pp_…` API tokens (hash lookup in `app.api_tokens`, expiry and revocation). It returns `AccessToken(token, client_id, scopes)`. `AuthSettings(issuer_url=<api base>, resource_server_url=<api base>/mcp, required_scopes=["mcp:read"])`. Missing or invalid tokens get `401` with `WWW-Authenticate`. |
| Transport security | `TransportSecuritySettings(allowed_hosts=MCP_ALLOWED_HOSTS, allowed_origins=MCP_ALLOWED_ORIGINS)` |
| Rate limit | 120 calls per minute per token |
| Audit | Every write tool call is recorded with `actor_type=integration` and the token name |

## Tools

Every tool delegates to the same domain services the LangGraph tools use. Inputs are
Pydantic models, and the SDK publishes them as JSON Schema. Outputs are structured content plus
a short text summary. Money is returned as minor units plus currency, and the text summary
formats it in `en-US`.

| Tool | Scope | Input | Output |
|---|---|---|---|
| `search_products` | `mcp:read` | `line` (`life`\|`health`\|`auto`), `must_have?: string[]`, `limit?: 1..10` | **Catalog discovery, no prices:** `products: [{id, code, carrier, name, line, keyAttributes, summary}]` |
| `quote_products` | `mcp:read` (quote-rate-limited) | A line-specific `inputs` object (`LifeQuoteInputs` \| `HealthQuoteInputs` \| `AutoQuoteInputs`, discriminated by `line`; schemas in catalog-and-rating.md), `max_monthly_budget_minor?`, `term_years?` (life) | Every eligible product of the line rated with those inputs, sorted by `monthlyEquivalent`, filtered by budget, plus `ineligible: [{productId, reasons[]}]`. The quotes are persisted with `source="mcp"`. |
| `compare_products` | `mcp:read` | `product_ids: string[2..4]` | `columns`, `rows: [{attribute, values[]}]`, `notes[]` |
| `get_quote` | `mcp:read` (quote-rate-limited) | `product_id` plus `inputs` (as above) | `quoteId, premium, billingPeriod, monthlyEquivalent, variants[], breakdown[], rateTableVersion, disclaimer, expiresAt`, or `eligible: false` with `reasons[]`. Persisted with `source="mcp"`. |
| `search_policy_documents` | `mcp:read` | `query`, `product_id?`, `limit?: 1..8` | `chunks: [{chunkId, documentTitle, section, pages, text}]` |
| `get_application_status` | `mcp:read` | `application_id` | `{status, productName, updatedAt}`. No applicant details. |
| `create_lead` | `mcp:write` | `name`, `email`, `phone_e164?`, `line_interest[]`, `notes?` (max 500), `consent_contact: true` (required), `consent_text_version` (the notice version the calling agent showed the person) | `{leadId, status}`. It creates a `contact_request` lead with `contact_consent_source="mcp"`, the token name and a timestamp as consent evidence, and emits `lead.created`. |

Free-text inputs (`notes`) pass through ingress sanitisation (security-and-privacy.md). Anything
redacted is reported back in `redactions` so the calling agent knows. Quote-producing calls are
limited to 60 per hour per token.

## Resources and prompts

| Kind | Name | Content |
|---|---|---|
| Resource | `catalog://products` | The active catalog as JSON: products, carriers and attribute definitions |
| Resource template | `catalog://products/{product_id}` | One product with its texts and current rate table version, but not the factors |
| Prompt | `needs-analysis` | Arguments `line`, `customer_context`. A system and user message pair that guides an agent through the agency's needs-analysis questions |

## Client configuration examples

Claude Code:

```bash
claude mcp add --transport http policypilot http://localhost:8080/mcp --header "Authorization: Bearer $POLICYPILOT_MCP_TOKEN"
```

Claude Desktop (`claude_desktop_config.json`) uses the remote-server entry with the same URL and
header. Local development uses the token from `.env` (`MCP_DEV_TOKEN`), which `make seed`
registers.

## Example demo

> "Using PolicyPilot, compare three term life products for a 40-year-old non-smoker with
> $500,000 coverage over 20 years and a $60 monthly budget."

The client calls `quote_products(inputs={line:"life", age:40, coverage_minor:50000000},
term_years=20, max_monthly_budget_minor=6000)`, then `compare_products` on the results. The
prices come from the deterministic engine (ADR-0003), and each quote ID can be checked in the
dashboard. With the seed data, no 20-year product fits a $60 budget for $500,000 of cover: both are
about $73–75 a month. The tool returns an empty list and the cheapest result over budget, so the
client can explain the trade-off.

## Tests

- Unit tests: the token verifier (valid, expired, revoked, wrong scope).
- Integration tests: they start the ASGI app in-process and use the SDK's Streamable HTTP client to
  list tools, call `search_products` and `get_quote`, and confirm that `create_lead` without
  `mcp:write` is denied.
- `scripts/mcp_smoke.py` runs the same calls against a running stack (`make mcp-smoke`).
