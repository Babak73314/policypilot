"""The application and contact-request flow (docs/design/agent-design.md#application-state-machine).

Deterministic: every prompt is a template, contact details are parsed from the sanitised stored
text by code (never by a model and never kept in graph state), and consent evidence is recorded
on the lead. Submitting freezes an immutable snapshot and hands over to `prepare_review`.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.agents.context import AgentContext
from policypilot.agents.nodes import extract_profile
from policypilot.agents.state import ConversationState
from policypilot.db.models import Application, Customer, Quote
from policypilot.domain.profile import CustomerProfile, MissingFieldsError, rating_inputs
from policypilot.domain.rating.models import ProductLine
from policypilot.i18n.formatting import format_money
from policypilot.services.applications import create_lead as create_lead_record
from policypilot.services.applications import (
    freeze_snapshot,
    mask_contact,
    parse_contact,
    start_application,
    update_contact,
)
from policypilot.services.catalog import get_product
from policypilot.services.quoting import quote_product

YES = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|agree|i agree|go ahead|please do|submit)\b", re.I)
SUBMIT = re.compile(r"\b(yes|submit|go ahead|send)\b", re.I)
# Any negation wins: "no, do not submit" must never read as consent or as a submission.
NO = re.compile(
    r"\b(no|nope|not|don'?t|do not|never|decline|cancel|stop|wait|hold on|won'?t)\b|n't\b", re.I
)


def affirmative(text: str, pattern: re.Pattern[str] = YES) -> bool:
    """True only for an unambiguous yes: an affirmative word and no negation anywhere."""
    return bool(pattern.search(text)) and not NO.search(text)


CHANGE = re.compile(r"\b(change|update|edit|wrong|correct)\b", re.I)


def _profile_of(state: ConversationState) -> CustomerProfile:
    return CustomerProfile.model_validate(state.get("profile") or {})


async def _requote_if_stale(
    ctx: AgentContext, session: AsyncSession, quote: Quote, profile: CustomerProfile
) -> Quote | None:
    """The same quote while it is valid for the profile; else a fresh one (None if ineligible)."""
    product = await get_product(session, quote.product_id, ctx.locale)
    if product is None:
        return None
    try:
        inputs = rating_inputs(profile, cast(ProductLine, product.line))
    except MissingFieldsError:
        inputs = None
    expired = quote.expires_at <= datetime.now(UTC)
    if not expired and (inputs is None or inputs.model_dump(mode="json") == quote.inputs):
        return quote
    if inputs is None:
        return None
    outcome = await quote_product(
        session,
        product,
        inputs,
        source="assistant",
        conversation_id=ctx.conversation_id,
        customer_id=ctx.customer_id,
        idempotency_scope=str(ctx.turn_id),
    )
    return outcome.quote


def _say(ctx: AgentContext, key: str, **params: str) -> str:
    return ctx.templates.text(key, ctx.channel, ctx.locale, **params)


def _needs_summary(profile: dict[str, Any], currency: str, locale: str) -> str:
    parts: list[str] = []
    if profile.get("age"):
        parts.append(f"age {profile['age']}")
    if profile.get("coverage_goal_minor"):
        parts.append(f"cover {format_money(profile['coverage_goal_minor'], currency, locale)}")
    if profile.get("term_years"):
        parts.append(f"{profile['term_years']}-year term")
    if profile.get("dependents") is not None:
        parts.append(f"{profile['dependents']} dependants")
    if profile.get("region"):
        parts.append(f"{profile['region']} region")
    if profile.get("vehicle_class"):
        parts.append(f"{profile['vehicle_class']} vehicle")
    if profile.get("monthly_budget_minor"):
        parts.append(
            f"budget {format_money(profile['monthly_budget_minor'], currency, locale)}/month"
        )
    return ", ".join(parts) or "as discussed"


def _choose(state: ConversationState, text: str) -> dict[str, Any] | None:
    offered: list[dict[str, Any]] = list(state.get("offered_quotes") or [])
    action = str(state.get("action") or "")
    if action.startswith("choose:"):
        return next((o for o in offered if o["quote_id"] == action[7:]), None)
    lowered = text.lower()
    named = [o for o in offered if o["product_name"].lower() in lowered]
    if len(named) == 1:
        return named[0]
    carrier = [o for o in offered if o["carrier_name"].split()[0].lower() in lowered]
    if len(carrier) == 1:
        return carrier[0]
    return offered[0] if len(offered) == 1 else None


async def application(state: ConversationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    app: dict[str, Any] = dict(state.get("application") or {})
    intent = str((state.get("route") or {}).get("intent", "apply"))
    action = str(state.get("action") or "")
    text = ctx.stored_text
    if not app:
        app = {"kind": "contact_request" if intent == "contact_request" else "application"}
    kind = app["kind"]
    update: dict[str, Any] = {"templated": True}

    # ---- choose the product (applications only)
    if kind == "application" and not app.get("quote_id"):
        offered = list(state.get("offered_quotes") or [])
        if not offered:
            return {**update, "draft": _say(ctx, "app_need_quote"), "application": None}
        chosen = _choose(state, text)
        if chosen is None:
            options = "\n".join(
                f"- {o['product_name']} by {o['carrier_name']}" for o in offered[:3]
            )
            return {
                **update,
                "draft": _say(ctx, "app_choose_product", options=options),
                "actions": [
                    {"id": f"choose:{o['quote_id']}", "label": o["product_name"]}
                    for o in offered[:3]
                ],
                "application": {**app, "step": "choose_product"},
            }
        app.update(
            quote_id=chosen["quote_id"],
            product_id=chosen["product_id"],
            product_name=chosen["product_name"],
            step="collect_contact",
        )

    step = app.get("step") or "collect_contact"

    # ---- corrections after the summary (or after an agent asked for changes)
    offered_details = parse_contact(text)
    has_details = bool(offered_details.name or offered_details.email or offered_details.phone)
    if step == "confirm_submit" and (
        action == "change_details" or bool(CHANGE.search(text)) or has_details
    ):
        details = offered_details
        if not (details.name or details.email or details.phone):
            return {
                **update,
                "draft": _say(
                    ctx,
                    "app_collect_contact",
                    product=app.get("product_name") or "your request",
                    missing="the name, email address or phone number you would like us to use",
                ),
                "application": {**app, "summary_shown": False},
            }
        async with ctx.db.transaction() as session:
            await update_contact(session, ctx.customer_id, details)
        app["summary_shown"] = False

    # ---- contact details
    if step == "collect_contact":
        details = parse_contact(text)
        async with ctx.db.transaction() as session:
            customer = await update_contact(session, ctx.customer_id, details)
            missing = []
            if not customer.display_name:
                missing.append("your name")
            if not (customer.email or customer.phone_e164):
                missing.append("an email address or phone number")
        if missing:
            product = app.get("product_name") or "your request"
            return {
                **update,
                "draft": _say(
                    ctx, "app_collect_contact", product=product, missing=" and ".join(missing)
                ),
                "application": {**app, "step": "collect_contact"},
            }
        return {
            **update,
            "draft": _say(ctx, "app_consent"),
            "actions": [{"id": "consent_yes", "label": "Yes"}, {"id": "consent_no", "label": "No"}],
            "application": {**app, "step": "consent"},
        }

    # ---- consent
    if step == "consent":
        yes = action == "consent_yes" or (action != "consent_no" and affirmative(text))
        no = action == "consent_no" or (not yes and bool(NO.search(text)))
        if no:
            return {**update, "draft": _say(ctx, "consent_declined"), "application": None}
        if not yes:
            return {
                **update,
                "draft": _say(ctx, "app_consent"),
                "actions": [
                    {"id": "consent_yes", "label": "Yes"},
                    {"id": "consent_no", "label": "No"},
                ],
                "application": app,
            }
        profile = dict(state.get("profile") or {})
        async with ctx.db.transaction() as session:
            owner = await session.get(Customer, ctx.customer_id)
            assert owner is not None
            lead = await create_lead_record(
                session,
                customer_id=ctx.customer_id,
                conversation_id=ctx.conversation_id,
                kind=kind,
                source=ctx.channel,
                line_interest=list(profile.get("lines") or []),
                profile=profile,
                consent_source=ctx.channel,
                first_name=owner.display_name,
            )
            if kind == "contact_request":
                return {**update, "draft": _say(ctx, "contact_request_done"), "application": None}
            quote = await session.get(Quote, uuid.UUID(app["quote_id"]))
            if quote is None:
                return {**update, "draft": _say(ctx, "app_need_quote"), "application": None}
            record = await start_application(
                session, lead=lead, conversation_id=ctx.conversation_id, quote=quote
            )
        app.update(application_id=str(record.id), lead_id=str(lead.id), step="confirm_submit")
        step = "confirm_submit"

    # ---- summary and submission
    if step == "confirm_submit":
        submit = action == "submit" or (action == "" and affirmative(text, SUBMIT))
        if action == "talk_to_agent":
            return {
                **update,
                "application": app,
                "route": {**(state.get("route") or {}), "intent": "human"},
            }
        current = _profile_of(state)
        if not submit and action == "":
            # A correction such as "actually I'm 50" at the summary reaches the profile.
            current = await extract_profile(ctx, state)
            update["profile"] = current.model_dump()
        async with ctx.db.transaction() as session:
            draft_record = await session.get(Application, uuid.UUID(app["application_id"]))
            applicant = await session.get(Customer, ctx.customer_id)
            quote = await session.get(Quote, uuid.UUID(app["quote_id"]))
            assert draft_record is not None and applicant is not None and quote is not None
            fresh = await _requote_if_stale(ctx, session, quote, current)
            if fresh is None:
                return {**update, "draft": _say(ctx, "app_need_quote"), "application": None}
            if fresh.id != quote.id:
                # Review 02, finding 11: never submit a price for inputs the customer has since
                # changed, or an expired quote. The new price must be confirmed again.
                quote, submit = fresh, False
                draft_record.quote_id = quote.id
                app.update(quote_id=str(quote.id), summary_shown=False)
            if submit and app.get("summary_shown"):
                snapshot = await freeze_snapshot(
                    session,
                    draft_record,
                    customer=applicant,
                    needs_profile=current.model_dump(),
                    created_by="customer",
                )
                return {
                    **update,
                    "application": {
                        **app,
                        "step": "submitted",
                        "snapshot_version": snapshot.version,
                    },
                    "submit_application": True,
                }
            summary = _say(
                ctx,
                "app_summary",
                product=app.get("product_name") or "the selected product",
                price=f"{{{{quote:{quote.id}}}}}",
                needs=_needs_summary(current.model_dump(), quote.currency, ctx.locale),
                contact=mask_contact(applicant.email, applicant.phone_e164),
            )
        return {
            **update,
            "draft": summary,
            "turn_quote_ids": [str(quote.id)],
            "actions": [
                {"id": "submit", "label": "Submit"},
                {"id": "change_details", "label": "Change details"},
                {"id": "talk_to_agent", "label": "Talk to agent"},
            ],
            "application": {**app, "summary_shown": True},
        }
    return {**update, "draft": _say(ctx, "generic_fallback"), "application": app}


def after_application(state: ConversationState) -> str:
    if state.get("submit_application"):
        return "prepare_review"
    if (state.get("route") or {}).get("intent") == "human" and not state.get("draft"):
        return "handoff"
    return "guard_output"
