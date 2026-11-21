"""The advisor's tools: thin wrappers over the domain services the MCP server also uses.

Rating inputs always come from the validated profile; the model can only choose products and pass
a cover amount the customer stated (agent-design.md#tools-advisor). Everything a tool returns is
recorded so the output guardrails can check the reply against it.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any, cast

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ValidationError

from policypilot.agents.context import AgentContext
from policypilot.agents.guardrails import literal_amounts, money_to_minor
from policypilot.agents.schemas import (
    CompareProducts,
    GetQuote,
    QuoteProducts,
    SearchPolicyDocuments,
    SearchProducts,
)
from policypilot.domain.profile import CustomerProfile, MissingFieldsError, rating_inputs
from policypilot.domain.rating.models import ProductLine
from policypilot.i18n.formatting import format_money
from policypilot.rag.retrieval import retrieve
from policypilot.services.catalog import ProductView, get_product, search_products
from policypilot.services.quoting import (
    quote_artifact,
    quote_product,
    quote_products,
    quote_tool_view,
)

ATTRIBUTE_LABELS = {
    "term_years": "Term (years)",
    "coverage_min_minor": "Minimum cover",
    "coverage_max_minor": "Maximum cover",
    "max_issue_age": "Maximum age at start",
    "conversion_option": "Conversion option",
    "accelerated_benefit": "Terminal illness advance",
    "tier": "Tier",
    "deductible_minor": "Deductible",
    "out_of_pocket_max_minor": "Out-of-pocket maximum",
    "coinsurance_pct": "Coinsurance (%)",
    "network": "Network",
    "telehealth": "Telehealth",
    "dental_addon": "Dental add-on",
    "coverage_level": "Coverage level",
    "liability_limit_minor": "Liability limit",
    "collision_deductible_minor": "Collision deductible",
    "comprehensive": "Theft, fire and weather",
    "roadside": "Roadside assistance",
    "rental_car": "Rental car",
}

TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "search_products": SearchProducts,
    "quote_products": QuoteProducts,
    "get_quote": GetQuote,
    "compare_products": CompareProducts,
    "search_policy_documents": SearchPolicyDocuments,
}
STATUS_LABELS = {
    "search_products": ("searching", "Looking through the catalog…"),
    "quote_products": ("quoting", "Calculating estimates…"),
    "get_quote": ("quoting", "Calculating an estimate…"),
    "compare_products": ("comparing", "Comparing products…"),
    "search_policy_documents": ("reading", "Reading the policy wording…"),
}


def tool_specs() -> list[BaseTool]:
    """Schemas for binding. Execution never goes through these; `AdvisorTools` dispatches."""

    async def _never(**_: Any) -> str:
        raise RuntimeError("advisor tools are dispatched by AdvisorTools")

    return [
        StructuredTool.from_function(
            coroutine=_never,
            name=name,
            description=(schema.__doc__ or name).strip(),
            args_schema=schema,
        )
        for name, schema in TOOL_SCHEMAS.items()
    ]


def display_attribute(key: str, value: Any, currency: str, locale: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key.endswith("_minor") and isinstance(value, int):
        return format_money(value, currency, locale)
    return str(value)


_BARE_AMOUNT = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s?(k|m|thousand|million)\b", re.I)


def stated_amounts(text: str) -> set[int]:
    """Amounts in minor units a customer wrote: "$500k", "500,000 dollars", "250k"."""
    amounts = set(literal_amounts(text))
    for raw, suffix in _BARE_AMOUNT.findall(text):
        minor = money_to_minor(raw, suffix)
        if minor is not None:
            amounts.add(minor)
    return amounts


class AdvisorTools:
    def __init__(
        self,
        ctx: AgentContext,
        profile: CustomerProfile,
        emit: Callable[[dict[str, Any]], None],
        *,
        scope_product_ids: list[str] | None = None,
        customer_text: str = "",
    ) -> None:
        self.ctx = ctx
        self.profile = profile
        self.emit = emit
        # Products the customer named this turn: the default scope of policy-document searches.
        self.scope_product_ids = [uuid.UUID(p) for p in scope_product_ids or []]
        # The customer's latest message: the only source a coverage override may come from.
        self.customer_text = customer_text
        self.quote_ids: list[str] = []
        self.chunk_ids: list[int] = []
        self.chunk_products: dict[str, str] = {}
        self.product_ids: list[str] = []
        self.artifacts: list[dict[str, Any]] = []
        self.calls = 0

    def _remember_products(self, products: list[ProductView]) -> None:
        for product in products:
            if str(product.id) not in self.product_ids:
                self.product_ids.append(str(product.id))

    async def execute(self, call: dict[str, Any]) -> ToolMessage:
        name = call["name"]
        started = time.monotonic()
        self.calls += 1
        key, label = STATUS_LABELS.get(name, ("working", "Working…"))
        self.emit({"type": "status", "key": key, "label": label})
        try:
            schema = TOOL_SCHEMAS[name]
            args = schema.model_validate(call.get("args") or {})
            result = await getattr(self, f"_{name}")(args)
            status = "success"
        except KeyError:
            result, status = {"error": f"unknown tool {name}"}, "error"
        except ValidationError as exc:
            result, status = {"error": "invalid arguments", "detail": exc.errors()[:3]}, "error"
        except MissingFieldsError as exc:
            result, status = {"error": "missing_fields", "fields": exc.fields}, "error"
        except LookupError as exc:
            result, status = {"error": str(exc)}, "error"
        self.ctx.trace.step(
            "tool", name, started, summary={"status": status, "keys": sorted(result)[:8]}
        )
        return ToolMessage(
            content=json.dumps(result, default=str),
            tool_call_id=call["id"],
            name=name,
            status=status,
        )

    # ------------------------------------------------------------------ tools

    async def _search_products(self, args: SearchProducts) -> dict[str, Any]:
        async with self.ctx.db.session() as session:
            products = await search_products(
                session, line=args.line, locale=self.ctx.locale, must_have=args.must_have or None
            )
        self._remember_products(products)
        return {"products": [p.brief() for p in products]}

    async def _quote_products(self, args: QuoteProducts) -> dict[str, Any]:
        line: ProductLine = args.line
        inputs = rating_inputs(self.profile, line)
        budget = (
            round(args.max_monthly_budget * 100)
            if args.max_monthly_budget is not None
            else self.profile.monthly_budget_minor
        )
        async with self.ctx.db.transaction() as session:
            within, ineligible, over = await quote_products(
                session,
                line=line,
                inputs=inputs,
                source="assistant",
                locale=self.ctx.locale,
                term_years=self.profile.term_years if line == "life" else None,
                max_monthly_budget_minor=budget,
                conversation_id=self.ctx.conversation_id,
                customer_id=self.ctx.customer_id,
                idempotency_scope=str(self.ctx.turn_id),
            )
            views = []
            for outcome in within[:3]:
                assert outcome.quote is not None
                self._record_quote(outcome.quote, outcome.product, over)
                views.append(
                    quote_tool_view(outcome.quote, outcome.product, locale=self.ctx.locale)
                )
        self._remember_products([o.product for o in within + ineligible])
        return {
            "quotes": views,
            "over_budget": over,
            "ineligible": [
                {"product": o.product.name, "reasons": list(o.ineligible_reasons)}
                for o in ineligible
            ],
        }

    async def _get_quote(self, args: GetQuote) -> dict[str, Any]:
        async with self.ctx.db.transaction() as session:
            product = await get_product(session, uuid.UUID(args.product_id), self.ctx.locale)
            if product is None:
                raise LookupError("unknown product_id")
            override = (
                round(args.coverage_override * 100) if args.coverage_override is not None else None
            )
            ignored = override is not None and override not in stated_amounts(self.customer_text)
            if ignored:
                override = None  # review 02, finding 12: never price an amount nobody stated
            line = cast(ProductLine, product.line)
            inputs = rating_inputs(self.profile, line, coverage_override_minor=override)
            outcome = await quote_product(
                session,
                product,
                inputs,
                source="assistant",
                conversation_id=self.ctx.conversation_id,
                customer_id=self.ctx.customer_id,
                idempotency_scope=str(self.ctx.turn_id),
            )
            self._remember_products([product])
            if outcome.quote is None:
                return {
                    "product": product.name,
                    "eligible": False,
                    "reasons": list(outcome.ineligible_reasons),
                }
            self._record_quote(outcome.quote, product, False)
            view = quote_tool_view(outcome.quote, product, locale=self.ctx.locale)
            if ignored:
                view["coverage_override_ignored"] = (
                    "the customer did not state that amount in this message; quoted their profile"
                )
            return view

    async def _compare_products(self, args: CompareProducts) -> dict[str, Any]:
        ids = list(dict.fromkeys(args.product_ids))[:4]
        if len(ids) < 2:
            raise LookupError("compare_products needs 2 to 4 distinct product ids")
        async with self.ctx.db.session() as session:
            products = [
                p
                for p in [await get_product(session, uuid.UUID(i), self.ctx.locale) for i in ids]
                if p is not None
            ]
        if len(products) < 2:
            raise LookupError("unknown product ids")
        self._remember_products(products)
        keys: list[str] = []
        for product in products:
            keys.extend(k for k in product.attributes if k not in keys)
        rows = [
            {
                "attribute": key,
                "label": ATTRIBUTE_LABELS.get(key, key.replace("_", " ").capitalize()),
                "values": [
                    display_attribute(key, p.attributes.get(key), p.currency, self.ctx.locale)
                    for p in products
                ],
            }
            for key in keys
        ]
        artifact = {
            "type": "comparison",
            "productIds": [str(p.id) for p in products],
            "columns": [
                {"productId": str(p.id), "productName": p.name, "carrierName": p.carrier_name}
                for p in products
            ],
            "rows": rows,
        }
        self.artifacts.append(artifact)
        self.emit({"type": "artifact", "artifact": artifact})
        return {
            "compared": [p.name for p in products],
            "rows": [{"label": r["label"], "values": r["values"]} for r in rows],
        }

    async def _search_policy_documents(self, args: SearchPolicyDocuments) -> dict[str, Any]:
        scope = [uuid.UUID(args.product_id)] if args.product_id else self.scope_product_ids
        async with self.ctx.db.session() as session:
            chunks = await retrieve(
                session,
                self.ctx.embedder,
                args.query,
                locale=self.ctx.locale,
                product_ids=scope or None,
                limit=6,
            )
        for chunk in chunks:
            if chunk.chunk_id not in self.chunk_ids:
                self.chunk_ids.append(chunk.chunk_id)
            self.chunk_products[str(chunk.chunk_id)] = str(chunk.product_id)
        return {
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "marker": f"[doc:{c.chunk_id}]",
                    "product_id": str(c.product_id),
                    "document": c.document_title,
                    "section": c.section_ref,
                    "pages": c.pages,
                    "text": f"<document id={c.chunk_id}>{c.content}</document>",
                }
                for c in chunks
            ]
        }

    def _record_quote(self, quote: Any, product: ProductView, over_budget: bool) -> None:
        if str(quote.id) not in self.quote_ids:
            self.quote_ids.append(str(quote.id))
            artifact = quote_artifact(
                quote, product, locale=self.ctx.locale, over_budget=over_budget
            )
            self.artifacts.append(artifact)
            self.emit({"type": "artifact", "artifact": artifact})
