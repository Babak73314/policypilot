"""The conversation graph (docs/design/agent-design.md#graph)."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from policypilot.agents import nodes, review_nodes
from policypilot.agents.application import after_application, application
from policypilot.agents.context import AgentContext
from policypilot.agents.state import ConversationState


def build_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph: StateGraph[Any, Any, Any, Any] = StateGraph(
        ConversationState, context_schema=AgentContext
    )
    graph.add_node("prepare_turn", nodes.prepare_turn)
    graph.add_node("route", nodes.route)
    graph.add_node("needs_analysis", nodes.needs_analysis)
    graph.add_node("advisor", nodes.advisor)
    graph.add_node("small_talk", nodes.small_talk)
    graph.add_node("guard_output", nodes.guard_output)
    graph.add_node("compact_history", nodes.compact_history)
    graph.add_node("application", application)
    graph.add_node("handoff", review_nodes.handoff)
    graph.add_node("prepare_review", review_nodes.prepare_review)
    graph.add_node("human_review", review_nodes.human_review)
    graph.add_node("finalize_review", review_nodes.finalize_review)

    graph.add_edge(START, "prepare_turn")
    graph.add_edge("prepare_turn", "route")
    graph.add_conditional_edges(
        "route",
        nodes.after_route,
        ["needs_analysis", "advisor", "small_talk", "application", "handoff"],
    )
    graph.add_conditional_edges(
        "application", after_application, ["prepare_review", "handoff", "guard_output"]
    )
    graph.add_edge("handoff", "prepare_review")
    graph.add_edge("prepare_review", "human_review")
    graph.add_edge("human_review", "finalize_review")
    graph.add_edge("finalize_review", "guard_output")
    graph.add_conditional_edges("needs_analysis", nodes.after_needs, ["advisor", "guard_output"])
    graph.add_edge("advisor", "guard_output")
    graph.add_edge("small_talk", "guard_output")
    graph.add_edge("guard_output", "compact_history")
    graph.add_edge("compact_history", END)
    return graph.compile(checkpointer=checkpointer)
