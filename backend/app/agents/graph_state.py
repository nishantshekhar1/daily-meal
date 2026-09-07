"""LangGraph state for the meal planner."""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class PlannerState(TypedDict, total=False):
    """Serializable planning state flowing through the graph.

    DB session and LLM client are injected via RunnableConfig.configurable,
    not stored here (keeps the graph exportable / checkpoint-friendly).
    """

    # Inputs
    slot: str
    session_id: str
    member_ids: list[int]

    # Context (built in load_context)
    pantry_text: str
    household_text: str
    total_adults: int
    total_toddlers: int
    has_toddler: bool
    min_toddler_age: Optional[int]
    cuisine_priority: list[str]
    web_context: str        # recipe ideas from web search; "" when disabled/unavailable

    # Intermediate
    candidate_dishes: list[dict[str, Any]]
    feasible_dishes: list[dict[str, Any]]
    pending_questions: list[dict[str, Any]]
    safety_reports: list[dict[str, Any]]

    # Outputs
    plan_id: Optional[int]
    planned_meals: list[dict[str, Any]]
    message: Optional[str]
    error: Optional[str]
