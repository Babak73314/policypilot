"""The MCP server (ADR-0010, docs/design/mcp-server.md).

Official `mcp` SDK (`MCPServer`), Streamable HTTP at /mcp, mounted in the FastAPI app. Tools are
thin wrappers over the same domain services the LangGraph advisor uses, so there is one set of
rules. Auth: opaque `pp_` API tokens checked by a TokenVerifier; write tools need `mcp:write`.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from policypilot import __version__
from policypilot.config import Settings
from policypilot.db.models import Application, Customer, ProductText
from policypilot.db.session import Database
from policypilot.domain.rating.models import AutoQuoteInputs, HealthQuoteInputs, LifeQuoteInputs
from policypilot.rag.embeddings import Embedder
from policypilot.rag.retrieval import retrieve
from policypilot.security.ingress import sanitize
from policypilot.services.applications import create_lead
from policypilot.services.catalog import get_product, list_products, search_products
from policypilot.services.events import nudge_dispatcher
from policypilot.services.quoting import quote_artifact, quote_product, quote_products
from policypilot.services.tokens import verify_token

QuoteInputs = Annotated[
    LifeQuoteInputs | HealthQuoteInputs | AutoQuoteInputs, Field(discriminator="line")
]


class LeadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone_e164: str | None = Field(default=None, pattern=r"^\+?[0-9]{7,15}$")
    line_interest: list[Literal["life", "health", "auto"]] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=500)
    consent_contact: Literal[True]
    consent_text_version: str = Field(min_length=1, max_length=64)


class ApiTokenVerifier:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def verify_token(self, token: str) -> AccessToken | None:
        async with self.db.transaction() as session:
            principal = await verify_token(session, token)
        if principal is None or not principal.scopes & {"mcp:read", "mcp:write"}:
            return None
        return AccessToken(token=token, client_id=principal.name, scopes=sorted(principal.scopes))


class _QuoteLimiter:
    """60 quote-producing calls per hour per token (security-and-privacy.md)."""

    def __init__(self, limit: int = 60, window: float = 3600) -> None:
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str) -> None:
        now, hits = time.monotonic(), self.hits[client]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise ToolError("quote rate limit reached for this token; try again later")
        hits.append(now)


def _client() -> AccessToken:
    token = get_access_token()
    if token is None:
        raise ToolError("authentication required")
    return token


def _require(scope: str) -> AccessToken:
    token = _client()
    if scope not in token.scopes:
        raise ToolError(f"this tool needs the {scope} scope")
    return token


def build_mcp_server(settings: Settings, db: Database, embedder: Embedder) -> MCPServer:
    base_url = settings.app_base_url.rstrip("/")
    mcp = MCPServer(
        name="PolicyPilot",
        version=__version__,
        instructions=(
            "Tools of a fictional multi-carrier insurance agency. Prices come only from the "
            "deterministic rating engine and are estimates, not offers."
        ),
        token_verifier=ApiTokenVerifier(db),
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            required_scopes=[],
            # Opaque API tokens carry no audience; the verifier checks them against the database.
            validate_token_resource=False,
        ),
    )
    limiter = _QuoteLimiter()

    @mcp.tool(
        name="search_products", description="Catalog discovery without prices: products of a line."
    )
    async def search_products_tool(
        line: Literal["life", "health", "auto"], must_have: list[str] | None = None, limit: int = 6
    ) -> dict[str, Any]:
        _require("mcp:read")
        async with db.session() as session:
            products = await search_products(session, line=line, must_have=must_have, limit=limit)
        return {"products": [p.brief() for p in products]}

    @mcp.tool(
        name="quote_products",
        description=(
            "Price every eligible product of a line for the given inputs, cheapest first, "
            "optionally within a monthly budget (minor units). Quotes are persisted."
        ),
    )
    async def quote_products_tool(
        inputs: QuoteInputs,
        max_monthly_budget_minor: int | None = None,
        term_years: Literal[10, 20, 30] | None = None,
    ) -> dict[str, Any]:
        token = _require("mcp:read")
        limiter.check(token.client_id)
        async with db.transaction() as session:
            within, ineligible, over = await quote_products(
                session,
                line=inputs.line,
                inputs=inputs,
                source="mcp",
                term_years=term_years if inputs.line == "life" else None,
                max_monthly_budget_minor=max_monthly_budget_minor,
            )
            return {
                "quotes": [
                    quote_artifact(o.quote, o.product, locale="en")
                    for o in within
                    if o.quote is not None
                ],
                "overBudget": over,
                "ineligible": [
                    {"productId": str(o.product.id), "reasons": list(o.ineligible_reasons)}
                    for o in ineligible
                ],
            }

    @mcp.tool(
        name="get_quote",
        description="Price one product for the given inputs. The quote is persisted.",
    )
    async def get_quote_tool(product_id: str, inputs: QuoteInputs) -> dict[str, Any]:
        token = _require("mcp:read")
        limiter.check(token.client_id)
        async with db.transaction() as session:
            product = await get_product(session, uuid.UUID(product_id))
            if product is None:
                raise ToolError("unknown product_id")
            outcome = await quote_product(session, product, inputs, source="mcp")
            if outcome.quote is None:
                return {"eligible": False, "reasons": list(outcome.ineligible_reasons)}
            return quote_artifact(outcome.quote, product, locale="en")

    @mcp.tool(name="compare_products", description="Side-by-side attributes of 2 to 4 products.")
    async def compare_products_tool(product_ids: list[str]) -> dict[str, Any]:
        _require("mcp:read")
        ids = [uuid.UUID(i) for i in dict.fromkeys(product_ids)][:4]
        if len(ids) < 2:
            raise ToolError("compare_products needs 2 to 4 product ids")
        async with db.session() as session:
            products = await list_products(session, product_ids=ids)
        keys = list(dict.fromkeys(k for p in products for k in p.attributes))
        return {
            "columns": [
                {"productId": str(p.id), "productName": p.name, "carrier": p.carrier_name}
                for p in products
            ],
            "rows": [
                {"attribute": k, "values": [p.attributes.get(k) for p in products]} for k in keys
            ],
        }

    @mcp.tool(
        name="search_policy_documents",
        description="Search the current policy wordings; cite chunkId, document and section.",
    )
    async def search_policy_documents_tool(
        query: str, product_id: str | None = None, limit: int = 6
    ) -> dict[str, Any]:
        _require("mcp:read")
        async with db.session() as session:
            chunks = await retrieve(
                session,
                embedder,
                query[:500],
                product_id=uuid.UUID(product_id) if product_id else None,
                limit=limit,
            )
        return {
            "chunks": [
                {
                    "chunkId": c.chunk_id,
                    "documentTitle": c.document_title,
                    "section": c.section_ref,
                    "pages": c.pages,
                    "text": c.content,
                }
                for c in chunks
            ]
        }

    @mcp.tool(
        name="get_application_status",
        description="Status of an application, without applicant details.",
    )
    async def get_application_status_tool(application_id: str) -> dict[str, Any]:
        _require("mcp:read")
        async with db.session() as session:
            application = await session.get(Application, uuid.UUID(application_id))
            if application is None:
                raise ToolError("unknown application_id")
            name = (
                await session.execute(
                    select(ProductText.name).where(
                        ProductText.product_id == application.product_id, ProductText.locale == "en"
                    )
                )
            ).scalar_one()
        return {
            "status": application.status,
            "productName": name,
            "updatedAt": application.updated_at.isoformat(),
        }

    @mcp.tool(
        name="create_lead",
        description="Create a contact-request lead. Requires explicit consent to be contacted.",
    )
    async def create_lead_tool(lead: LeadInput) -> dict[str, Any]:
        token = _require("mcp:write")
        notes = sanitize(lead.notes or "", source="mcp")
        async with db.transaction() as session:
            customer = Customer(
                display_name=lead.name, email=lead.email.lower(), phone_e164=lead.phone_e164
            )
            session.add(customer)
            await session.flush()
            record = await create_lead(
                session,
                customer_id=customer.id,
                conversation_id=None,
                kind="contact_request",
                source="mcp",
                line_interest=list(lead.line_interest),
                profile={
                    "notes": notes.stored_text,
                    "via": token.client_id,
                    "consentTextVersion": lead.consent_text_version,
                },
                consent_source="mcp",
                first_name=lead.name,
            )
        nudge_dispatcher()
        return {"leadId": str(record.id), "status": record.status, "redactions": notes.redactions}

    @mcp.resource(
        "catalog://products",
        mime_type="application/json",
        description="The active product catalog (no rate factors).",
    )
    async def catalog() -> list[dict[str, Any]]:
        async with db.session() as session:
            return [p.brief() for p in await list_products(session)]

    @mcp.prompt(name="needs-analysis", description="Guide a customer through needs analysis.")
    def needs_analysis(line: str, customer_context: str = "") -> str:
        return (
            f"You are helping an insurance agency customer choose {line} cover. Ask, one or two at "
            "a time, only for the facts needed to quote: for life the age, cover amount and term; "
            "for health the age, region and number of dependants; for auto the driver's age, "
            "vehicle class, yearly mileage and claims in the last 5 years. Never ask for health "
            "details, identifiers or payment data. Then call quote_products_tool. "
            f"Context so far: {customer_context or 'none'}"
        )

    return mcp


def transport_security(settings: Settings) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.mcp_allowed_hosts,
        allowed_origins=settings.mcp_allowed_origins,
    )
