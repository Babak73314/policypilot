"""Graph nodes for routing, needs analysis, advice and output validation (agent-design.md)."""

from __future__ import annotations

import contextlib
import re
import time
import uuid
from decimal import Decimal
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from pydantic import ValidationError
from sqlalchemy import select

from policypilot.agents import numbers, prompts
from policypilot.agents.context import AgentContext
from policypilot.agents.guardrails import TurnFacts, render, render_quote, validate
from policypilot.agents.models import MODEL_FAILURES
from policypilot.agents.schemas import ProfileDelta, RouteDecision
from policypilot.agents.state import ConversationState
from policypilot.agents.tools import AdvisorTools, tool_specs
from policypilot.db.models import DocumentChunk, PolicyDocument, Quote, RateTable
from policypilot.domain.profile import FIELD_HINTS, REQUIRED_FIELDS, CustomerProfile, missing_fields
from policypilot.domain.rating.models import ProductLine
from policypilot.services.catalog import list_products, mentioned_products

HISTORY_WINDOW = 12
MAX_STATE_MESSAGES = 40
SUMMARY_EVERY_TURNS = 10
MAX_TOOL_ROUNDS = 4
MAX_TOOL_CALLS = 8
HUMAN_WORDS = re.compile(
    r"\b(agent|human|real person|speak to (?:a|someone)|talk to (?:a|someone))\b", re.I
)
REDACTION_MARKER = re.compile(r"\[REDACTED:[a-z_]+\]")


# ============================================================================ helpers


def _ctx(runtime: Runtime[AgentContext]) -> AgentContext:
    return runtime.context


def _emit(event: dict[str, Any]) -> None:
    with contextlib.suppress(RuntimeError):  # not streaming (e.g. ainvoke in tests)
        get_stream_writer()(event)


def _strip_thinking(message: AIMessage) -> AIMessage:
    """Previous turns are replayed without thinking blocks; only the live tool loop keeps them."""
    if not isinstance(message.content, list):
        return message
    kept = [
        block
        for block in message.content
        if not (isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))
    ]
    return message.model_copy(update={"content": kept})


def model_history(messages: list[AnyMessage], window: int = HISTORY_WINDOW) -> list[BaseMessage]:
    """The last `window` messages, starting at a customer message so tool pairs stay intact."""
    recent = list(messages[-window:])
    while recent and not isinstance(recent[0], HumanMessage):
        recent.pop(0)
    if not recent:
        last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        recent = [last_human] if last_human else []
    return [_strip_thinking(m) if isinstance(m, AIMessage) else m for m in recent]


def _last_customer_text(state: ConversationState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _system(ctx: AgentContext, state: ConversationState, instruction: str) -> SystemMessage:
    context = prompts.turn_context(
        channel=ctx.channel,
        locale=ctx.locale,
        profile=state.get("profile") or {},
        history_summary=state.get("history_summary") or "",
    )
    return SystemMessage(
        content=prompts.base_prompt(ctx.settings.agency_name) + "\n" + context + "\n" + instruction
    )


def _profile(state: ConversationState) -> CustomerProfile:
    return CustomerProfile.model_validate(state.get("profile") or {})


def _delta_to_profile(delta: ProfileDelta) -> CustomerProfile:
    """Convert major units to minor units and drop any value that fails validation."""
    raw = delta.model_dump()
    candidate: dict[str, Any] = {
        "lines": raw["lines"] or [],
        "monthly_budget_minor": round(raw["monthly_budget"] * 100)
        if raw["monthly_budget"]
        else None,
        "coverage_goal_minor": round(raw["coverage_goal"] * 100) if raw["coverage_goal"] else None,
    }
    for key in (
        "age",
        "household_size",
        "dependents",
        "region",
        "term_years",
        "tier_preference",
        "driver_age",
        "vehicle_class",
        "annual_mileage_band",
        "claims_last_5y",
        "coverage_level",
    ):
        candidate[key] = raw.get(key)
    clean: dict[str, Any] = {}
    for key, value in candidate.items():
        if value in (None, []):
            continue
        try:
            CustomerProfile.model_validate({key: value})
            clean[key] = value
        except ValidationError:
            continue
    return CustomerProfile.model_validate(clean)


def _intent(state: ConversationState) -> str:
    return str((state.get("route") or {}).get("intent", "needs_analysis"))


def _as_line(value: Any) -> ProductLine | None:
    return cast(ProductLine, value) if value in ("life", "health", "auto") else None


def _line(state: ConversationState | dict[str, Any]) -> ProductLine | None:
    route_line = _as_line((state.get("route") or {}).get("line"))
    if route_line:
        return route_line
    lines = (state.get("profile") or {}).get("lines") or []
    return _as_line(lines[0]) if lines else None


# ============================================================================ nodes


async def prepare_turn(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = _ctx(runtime)
    return {
        "conversation_id": str(ctx.conversation_id),
        "turn_id": str(ctx.turn_id),
        "channel": ctx.channel,
        "locale": ctx.locale,
        "profile": state.get("profile") or {},
        "history_summary": state.get("history_summary") or "",
        "route": None,
        "turn_quote_ids": [],
        "turn_chunk_ids": [],
        "turn_chunk_products": {},
        "turn_product_ids": [],
        "artifacts": [],
        "citations": [],
        "actions": [],
        "draft": None,
        "ask_fields": [],
        "templated": False,
        "reply": None,
        "flags": {"redactions": dict(ctx.redactions), "guardrail_actions": []},
        "turn_count": int(state.get("turn_count") or 0) + 1,
        "submit_application": False,
        "review_request": None,
    }


async def route(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = _ctx(runtime)
    started = time.monotonic()
    text = _last_customer_text(state)
    remaining = REDACTION_MARKER.sub("", text).strip()
    decision: RouteDecision
    if ctx.redactions and len(remaining.split()) < 3:
        decision = RouteDecision(intent="greeting", line=None, confidence=1.0)
        result: dict[str, Any] = decision.model_dump() | {"intent": "sensitive_only"}
    elif HUMAN_WORDS.search(text):
        result = RouteDecision(intent="human", line=_line(state), confidence=1.0).model_dump()
    elif (state.get("application") or {}).get("status") == "changes_requested" or (
        state.get("application") or {}
    ).get("step") in ("choose_product", "collect_contact", "consent", "confirm_submit"):
        result = RouteDecision(intent="apply", line=_line(state), confidence=1.0).model_dump()
    else:
        try:
            runnable = ctx.models.structured("router", RouteDecision)
            decision = await runnable.ainvoke(
                [SystemMessage(content=prompts.ROUTER), HumanMessage(content=text)],
                config=ctx.llm_config(),
            )
            result = decision.model_dump()
            confidence = float(result.get("confidence") or 0)
            if confidence < 0.5:
                result["intent"] = "needs_analysis"
        except (ValidationError, ValueError):
            result = {"intent": "needs_analysis", "line": None, "confidence": 0.0}
        if not result.get("line"):
            lines = (state.get("profile") or {}).get("lines") or []
            result["line"] = lines[0] if lines else None
    result |= await _products_named(ctx, remaining, result.get("line"))
    ctx.trace.route = str(result["intent"])
    ctx.trace.step("node", "route", started, summary={"intent": result["intent"]})
    return {"route": result}


async def _products_named(ctx: AgentContext, text: str, line: str | None) -> dict[str, Any]:
    """Products the customer names (by carrier and product words), found by code, not a model.

    One match becomes the product in focus. Several (for example "the Kestrel policy") still
    narrow the policy-document search, and citations from other products fail the guardrail.
    """
    if not text:
        return {"product_id": None, "product_ids": [], "products_named": []}
    async with ctx.db.session() as session:
        named = mentioned_products(text, await list_products(session, locale=ctx.locale), line)
    return {
        "product_id": str(named[0].id) if len(named) == 1 else None,
        "product_ids": [str(p.id) for p in named],
        "products_named": [f"{p.name} ({p.id})" for p in named],
    }


async def extract_profile(ctx: AgentContext, state: ConversationState) -> CustomerProfile:
    """The profile merged with the facts stated in the latest messages (structured extraction)."""
    profile = _profile(state)
    try:
        extractor = ctx.models.structured("extractor", ProfileDelta)
        delta = await extractor.ainvoke(
            [SystemMessage(content=prompts.EXTRACTOR), *model_history(state["messages"], 4)],
            config=ctx.llm_config(),
        )
        profile = profile.merge(_delta_to_profile(delta))
    except (ValidationError, ValueError):
        pass
    return profile


def assumed_driver_age(profile: CustomerProfile) -> int | None:
    """For car insurance, the customer is the main driver unless they named another driver.

    "I'm 30, I drive a sedan" gives the customer's age; the extractor may record it only as
    `age`. When no driver age is known, the customer's own age is used.
    """
    if profile.driver_age is None and profile.age is not None:
        return profile.age
    return None


async def needs_analysis(
    state: ConversationState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    ctx = _ctx(runtime)
    started = time.monotonic()
    profile = await extract_profile(ctx, state)
    line = _line({**state, "profile": profile.model_dump()})
    update: dict[str, Any] = {"profile": profile.model_dump()}
    if line and line not in profile.lines:
        profile = profile.model_copy(update={"lines": sorted({*profile.lines, line})})
        update["profile"] = profile.model_dump()
    if line == "auto" and (driver := assumed_driver_age(profile)) is not None:
        profile = profile.model_copy(update={"driver_age": driver})
        update["profile"] = profile.model_dump()
    if line is None:
        update["ask_fields"] = ["the type of insurance: life, health or auto"]
        instruction = prompts.ASK_LINE
    else:
        missing = missing_fields(profile, line)
        if not missing:
            update["route"] = {**(state.get("route") or {}), "line": line, "profile_complete": True}
            ctx.trace.step("node", "needs_analysis", started, summary={"complete": True})
            return update
        hint_list = [FIELD_HINTS[name] for name in missing[:2]]
        update["ask_fields"] = hint_list
        instruction = prompts.ASK_FIELDS.format(line=line, fields="; ".join(hint_list))
    writer = ctx.models.plain("writer")
    message = await writer.ainvoke(
        [
            _system(ctx, cast(ConversationState, {**state, **update}), instruction),
            *model_history(state["messages"], 4),
        ],
        config=ctx.llm_config(),
    )
    update["draft"] = message_text(message)
    ctx.trace.step("node", "needs_analysis", started, summary={"complete": False})
    return update


def message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()


async def advisor(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = _ctx(runtime)
    started = time.monotonic()
    profile = _profile(state)
    line = _line(state) or "life"
    complete = not missing_fields(profile, line) if line in REQUIRED_FIELDS else False
    budget = profile.monthly_budget_minor
    route = state.get("route") or {}
    tools = AdvisorTools(
        ctx,
        profile,
        _emit,
        scope_product_ids=route.get("product_ids") or [],
        customer_text=_last_customer_text(state),
    )
    instruction = prompts.ADVISOR.format(
        intent=_intent(state),
        line=line,
        profile_complete="yes" if complete else "no",
        budget=f"{budget / 100:.2f}" if budget else "none",
        focus=route.get("product_id") or "none",
        named="; ".join(route.get("products_named") or []) or "none",
    )
    system = _system(ctx, state, instruction)
    history = model_history(state["messages"])
    new_messages: list[AnyMessage] = []
    runnable = ctx.models.with_tools("advisor", tool_specs())
    draft = ""
    for round_number in range(MAX_TOOL_ROUNDS + 1):
        final_round = round_number == MAX_TOOL_ROUNDS or tools.calls >= MAX_TOOL_CALLS
        if final_round:
            answer = await ctx.models.plain("advisor").ainvoke(
                [system, *history, *new_messages, HumanMessage(content=prompts.FINAL_ROUND)],
                config=ctx.llm_config(),
            )
            draft = message_text(answer)
            break
        ai = await runnable.ainvoke([system, *history, *new_messages], config=ctx.llm_config())
        if not getattr(ai, "tool_calls", None):
            draft = message_text(ai)
            break
        new_messages.append(ai)
        # Split once, before executing: `tools.calls` grows while the batch runs.
        allowed = max(0, MAX_TOOL_CALLS - tools.calls)
        executed, rejected = ai.tool_calls[:allowed], ai.tool_calls[allowed:]
        for call in executed:
            new_messages.append(await tools.execute(call))
        for call in rejected:
            new_messages.append(
                ToolMessage(
                    content='{"error": "tool budget reached"}',
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
            )
    ctx.trace.step("node", "advisor", started, summary={"tool_calls": tools.calls})
    return {
        "messages": new_messages,
        "draft": draft,
        "turn_quote_ids": tools.quote_ids,
        "turn_chunk_ids": tools.chunk_ids,
        "turn_chunk_products": tools.chunk_products,
        "turn_product_ids": tools.product_ids,
        "artifacts": tools.artifacts,
        **(
            {
                "offered_quotes": [
                    {
                        "quote_id": a["quoteId"],
                        "product_id": a["productId"],
                        "product_name": a["productName"],
                        "carrier_name": a["carrierName"],
                    }
                    for a in tools.artifacts
                    if a.get("type") == "quote"
                ]
            }
            if tools.quote_ids
            else {}
        ),
    }


async def small_talk(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = _ctx(runtime)
    intent = _intent(state)
    key = {
        "greeting": "greeting",
        "out_of_scope": "out_of_scope",
        "sensitive_only": None,
    }.get(intent, "feature_unavailable")
    draft = ctx.templates.text(key, ctx.channel, ctx.locale) if key else ""
    return {"draft": draft, "templated": True}


async def guard_output(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = _ctx(runtime)
    started = time.monotonic()
    draft = state.get("draft") or ""
    quote_ids = {q.lower() for q in state.get("turn_quote_ids") or []}
    chunk_ids = set(state.get("turn_chunk_ids") or [])
    profile = _profile(state)
    async with ctx.db.session() as session:
        quotes = (
            {
                str(q.id): q
                for q in (
                    await session.execute(
                        select(Quote).where(Quote.id.in_([uuid.UUID(q) for q in quote_ids]))
                    )
                ).scalars()
            }
            if quote_ids
            else {}
        )
        money, plain = await _number_facts(session, state, profile, quotes, ctx.locale)
        chunks = await _chunk_details(session, chunk_ids)
        product_names = await _product_names(session, {str(q.product_id) for q in quotes.values()})
        catalog = tuple(await list_products(session, locale=ctx.locale, active_only=False))
    facts = TurnFacts(
        quote_ids=quote_ids,
        chunk_ids=chunk_ids,
        chunk_products=dict(state.get("turn_chunk_products") or {}),
        allowed_amounts=money,
        focus_product_ids=frozenset((state.get("route") or {}).get("product_ids") or []),
        policy_question=_intent(state) == "product_question" and not state.get("templated"),
        budget_minor=profile.monthly_budget_minor,
        coverage_minor=profile.coverage_goal_minor,
        plain_numbers=frozenset(plain),
        check_numbers=not state.get("templated"),
        quote_products={qid: str(q.product_id) for qid, q in quotes.items()},
        catalog=catalog,
    )
    actions = list((state.get("flags") or {}).get("guardrail_actions") or [])

    check = validate(draft, facts, ctx.channel)
    if not check.ok and all(p.startswith("banned_claim") for p in check.problems):
        ctx.trace.guardrail("repair", ", ".join(check.problems))
        actions.append("repair")
        repaired = await ctx.models.plain("writer").ainvoke(
            [SystemMessage(content=prompts.REPAIR), HumanMessage(content=draft)],
            config=ctx.llm_config(),
        )
        draft = message_text(repaired)
        check = validate(draft, facts, ctx.channel)

    if not check.ok:
        # The offending sentences go to the run trace (masked there), so an agent or an eval
        # report can see why the model's reply was replaced.
        detail = ", ".join(check.problems)
        if check.evidence:
            detail += " | " + " / ".join(dict.fromkeys(check.evidence))
        ctx.trace.guardrail("template_fallback", detail)
        actions.append("template_fallback")
        draft = _fallback_text(ctx, state, quotes, product_names)
        reply, citations = draft, list[dict[str, Any]]()
    else:
        reply, citations = render(draft, quotes, chunks, ctx.locale)

    has_quotes = any(a.get("type") == "quote" for a in state.get("artifacts") or [])
    if has_quotes and "estimate" not in reply.lower():
        reply = f"{reply}\n\n{ctx.templates.text('quote_disclaimer', ctx.channel, ctx.locale)}"
        actions.append("disclaimer_added")
    redactions = (state.get("flags") or {}).get("redactions") or {}
    notices = []
    if any(k in redactions for k in ("card", "iban", "national_id")):
        notices.append(ctx.templates.text("sensitive_data_warning", ctx.channel, ctx.locale))
    if "health" in redactions:
        notices.append(ctx.templates.text("health_data_notice", ctx.channel, ctx.locale))
    if notices:
        reply = "\n\n".join([*notices, reply]).strip()
    ctx.trace.step("guardrail", "guard_output", started, summary={"actions": actions})
    return {
        "reply": reply,
        "citations": citations,
        "messages": [AIMessage(content=reply)],
        "flags": {**(state.get("flags") or {}), "guardrail_actions": actions},
    }


def _fallback_text(
    ctx: AgentContext,
    state: ConversationState,
    quotes: dict[str, Quote],
    product_names: dict[str, str],
) -> str:
    if quotes:
        lines = "\n".join(
            f"- {product_names.get(str(q.product_id), 'Product')}: {render_quote(q, ctx.locale)}"
            for q in quotes.values()
        )
        return ctx.templates.text("quote_fallback", ctx.channel, ctx.locale, quote_lines=lines)
    if state.get("ask_fields"):
        fields = " and ".join(state.get("ask_fields") or [])
        return ctx.templates.text("ask_fields", ctx.channel, ctx.locale, fields=fields)
    if _intent(state) == "product_question":
        return ctx.templates.text("insufficient_evidence", ctx.channel, ctx.locale)
    return ctx.templates.text("generic_fallback", ctx.channel, ctx.locale)


async def _number_facts(
    session: Any,
    state: ConversationState,
    profile: CustomerProfile,
    quotes: dict[str, Quote],
    locale: str,
) -> tuple[set[int], set[Decimal]]:
    """(money in minor units, plain numbers) that this turn's reply may state (guardrails).

    Sources: the profile, the attributes, texts and eligibility limits of the products involved
    this turn (returned by tools, quoted, or named by the customer), the policy text retrieved
    this turn, the field hints being asked, and the numbers the customer wrote (plain only). The
    budget and the cover goal are not here: they have their own sentence rules.
    """
    money: set[int] = set()
    plain: set[Decimal] = {
        Decimal(v)
        for v in (
            profile.age,
            profile.household_size,
            profile.dependents,
            profile.term_years,
            profile.driver_age,
            profile.claims_last_5y,
        )
        if v is not None
    }
    ids = {
        *(state.get("turn_product_ids") or []),
        *(str(q.product_id) for q in quotes.values()),
        *((state.get("route") or {}).get("product_ids") or []),
    }
    if ids:
        products = await list_products(
            session, locale=locale, active_only=False, product_ids=[uuid.UUID(i) for i in ids]
        )
        for product in products:
            for key, value in product.attributes.items():
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, bool) or not isinstance(item, int | float):
                        continue
                    if key.endswith("_minor"):
                        money.add(int(item))
                        plain.add(Decimal(int(item)) / 100)
                    else:
                        plain.add(Decimal(str(item)))
            texts = " ".join([product.summary, *product.highlights])
            more_plain, more_money = numbers.numbers_in(texts)
            plain |= more_plain
            money |= more_money
    chunk_ids = list(state.get("turn_chunk_ids") or [])
    if chunk_ids:
        rows = await session.execute(
            select(DocumentChunk.section_ref, DocumentChunk.content).where(
                DocumentChunk.id.in_(chunk_ids)
            )
        )
        for section, content in rows:
            more_plain, more_money = numbers.numbers_in(f"{section} {content}")
            plain |= more_plain
            money |= more_money
    if ids:  # eligibility limits of the products involved ("aged 18 to 64"), never prices
        tables = await session.execute(
            select(RateTable.factors).where(RateTable.product_id.in_([uuid.UUID(i) for i in ids]))
        )
        for factors in tables.scalars():
            for key, value in (factors or {}).items():
                if isinstance(value, int) and not isinstance(value, bool) and "_minor" not in key:
                    plain.add(Decimal(value))
    hints = numbers.numbers_in(" ".join(state.get("ask_fields") or []))[0]
    # What the customer said themselves ("about 12,000 miles a year"): plain numbers only. Their
    # money amounts keep their own rules (the budget and the cover goal).
    said = numbers.numbers_in(
        " ".join(str(m.content) for m in state.get("messages") or [] if isinstance(m, HumanMessage))
    )[0]
    return money, plain | hints | said


async def _chunk_details(session: Any, chunk_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not chunk_ids:
        return {}
    rows = await session.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.section_ref,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            PolicyDocument.title,
        )
        .join(PolicyDocument, PolicyDocument.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_(chunk_ids))
    )
    return {
        row.id: {
            "documentTitle": row.title,
            "section": row.section_ref,
            "pages": str(row.page_start)
            if row.page_start == row.page_end
            else f"{row.page_start}-{row.page_end}",
        }
        for row in rows
    }


async def _product_names(session: Any, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    from policypilot.services.catalog import list_products

    views = await list_products(session, product_ids=[uuid.UUID(i) for i in ids], active_only=False)
    return {str(v.id): f"{v.name} by {v.carrier_name}" for v in views}


async def compact_history(
    state: ConversationState, runtime: Runtime[AgentContext]
) -> dict[str, Any]:
    """Keep checkpoints bounded: stub old tool results, summarise and drop old messages."""
    ctx = _ctx(runtime)
    messages = list(state["messages"])
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    updates: list[AnyMessage] = []
    for message in messages[:last_human]:
        if isinstance(message, ToolMessage) and len(str(message.content)) > 120:
            updates.append(
                ToolMessage(
                    content="[result omitted; see stored quotes and documents]",
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                    id=message.id,
                )
            )
    result: dict[str, Any] = {"messages": updates} if updates else {}
    turn_count = int(state.get("turn_count") or 0)
    if len(messages) > MAX_STATE_MESSAGES or (
        turn_count % SUMMARY_EVERY_TURNS == 0 and len(messages) > 2 * HISTORY_WINDOW
    ):
        keep_from = len(messages) - HISTORY_WINDOW
        while keep_from < len(messages) and not isinstance(messages[keep_from], HumanMessage):
            keep_from += 1
        old = messages[:keep_from]
        if old:
            try:
                summary = await ctx.models.plain("writer").ainvoke(
                    [
                        SystemMessage(content=prompts.SUMMARISE_HISTORY),
                        *[
                            _strip_thinking(m) if isinstance(m, AIMessage) else m
                            for m in old
                            if not isinstance(m, ToolMessage) and not getattr(m, "tool_calls", None)
                        ],
                        HumanMessage(content="Summarise the conversation above."),
                    ],
                    config=ctx.llm_config(),
                )
            except MODEL_FAILURES:
                return result  # summarise on a later turn; the reply is already validated
            previous = state.get("history_summary") or ""
            result["history_summary"] = (previous + "\n" + message_text(summary)).strip()[-2000:]
            removals = [RemoveMessage(id=m.id) for m in old if m.id]
            result["messages"] = [u for u in updates if u.id not in {m.id for m in old}] + removals
    return result


# ============================================================================ routing


def after_route(state: ConversationState) -> str:
    intent = _intent(state)
    if intent in ("greeting", "out_of_scope", "sensitive_only"):
        return "small_talk"
    if intent in ("apply", "contact_request"):
        return "application"
    if intent == "human":
        return "handoff"
    if intent == "product_question":
        return "advisor"
    # Quote and compare also go through needs_analysis: a correction in the same message
    # ("actually I'm 50, quote again") must reach the profile before anything is priced.
    # needs_analysis hands over to the advisor once the profile is complete (after_needs).
    return "needs_analysis"


def after_needs(state: ConversationState) -> str:
    if (state.get("route") or {}).get("profile_complete") and not state.get("draft"):
        return "advisor"
    return "guard_output"
