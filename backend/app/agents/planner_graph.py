"""Compiled LangGraph for meal planning + Mermaid export helpers."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.graph_state import PlannerState
from app.agents.planner_nodes import (
    allocate_inventory,
    ask_clarifications,
    load_context,
    rank_dishes,
    route_after_allocate,
    route_after_shortlist,
    shortlist_dishes,
    write_recipes,
)


def build_planner_graph() -> Any:
    """Build (uncompiled) then compile the meal-planner StateGraph.

    Topology is independent of DB/LLM — those are passed at invoke time via
    ``config={"configurable": {"db": ..., "llm": ...}}``.
    """
    g: StateGraph = StateGraph(PlannerState)

    g.add_node("load_context", load_context)
    g.add_node("shortlist", shortlist_dishes)
    g.add_node("rank_by_cuisine", rank_dishes)
    g.add_node("allocate", allocate_inventory)
    g.add_node("ask_clarifications", ask_clarifications)
    g.add_node("write_recipes", write_recipes)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "shortlist")
    g.add_conditional_edges(
        "shortlist",
        route_after_shortlist,
        {
            "rank": "rank_by_cuisine",
            "empty": END,
        },
    )
    g.add_edge("rank_by_cuisine", "allocate")
    g.add_conditional_edges(
        "allocate",
        route_after_allocate,
        {
            "write_recipes": "write_recipes",
            "ask_clarifications": "ask_clarifications",
            "empty": END,
        },
    )
    g.add_edge("ask_clarifications", END)
    g.add_edge("write_recipes", END)

    return g.compile()


@lru_cache(maxsize=1)
def get_planner_graph():
    """Process-wide compiled graph (topology only)."""
    return build_planner_graph()


def planner_mermaid() -> str:
    """Return Mermaid flowchart source for the planner graph."""
    return get_planner_graph().get_graph().draw_mermaid()


def planner_mermaid_png() -> bytes:
    """Render the planner graph to PNG bytes (via Mermaid.ink by default)."""
    return get_planner_graph().get_graph().draw_mermaid_png()
