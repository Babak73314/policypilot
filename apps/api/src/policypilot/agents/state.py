"""Conversation graph state (docs/design/agent-design.md#state).

Everything is JSON-serialisable and none of it holds raw identifiers, emails or phone numbers:
the graph only ever receives sanitised `model_text`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ConversationState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    history_summary: NotRequired[str]
    conversation_id: NotRequired[str]
    turn_id: NotRequired[str]
    channel: NotRequired[Literal["web", "whatsapp"]]
    locale: NotRequired[str]
    profile: NotRequired[dict[str, Any]]
    route: NotRequired[dict[str, Any] | None]
    turn_quote_ids: NotRequired[list[str]]
    turn_chunk_ids: NotRequired[list[int]]
    turn_chunk_products: NotRequired[dict[str, str]]
    turn_product_ids: NotRequired[list[str]]
    artifacts: NotRequired[list[dict[str, Any]]]
    citations: NotRequired[list[dict[str, Any]]]
    actions: NotRequired[list[dict[str, str]]]
    application: NotRequired[dict[str, Any] | None]
    review: NotRequired[dict[str, Any] | None]
    flags: NotRequired[dict[str, Any]]
    draft: NotRequired[str | None]
    ask_fields: NotRequired[list[str]]
    templated: NotRequired[bool]
    reply: NotRequired[str | None]
    turn_count: NotRequired[int]
    action: NotRequired[str | None]
    offered_quotes: NotRequired[list[dict[str, Any]]]
    submit_application: NotRequired[bool]
    review_request: NotRequired[dict[str, Any] | None]
