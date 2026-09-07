"""Meal planning agent — LangGraph orchestration facade.

Graph topology (see ``planner_graph.py``):

  START → load_context → shortlist ─┬─→ allocate ─┬─→ write_recipes → END
                                    │             └─→ ask_clarifications → END
                                    └─→ END (empty shortlist)

LLM proposes dishes/recipes; Python owns inventory math and toddler safety.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.agents.planner_graph import get_planner_graph
from app.agents.planner_nodes import infer_slot
from app.models import HouseholdMember, MealSlot
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# Re-export for API routes that import MealSlot from planner
__all__ = ["MealPlannerAgent", "MealSlot"]


class MealPlannerAgent:
    """Thin facade that invokes the compiled LangGraph planner."""

    def __init__(self, db: Session, llm: LLMClient) -> None:
        self._db = db
        self._llm = llm
        self._graph = get_planner_graph()

    async def suggest(
        self,
        slot: Optional[MealSlot] = None,
        members: Optional[list[HouseholdMember]] = None,
        planning_session_id: Optional[str] = None,
    ) -> dict:
        """Run the planner graph and return the API-shaped result dict."""
        if members is None:
            members = list(
                self._db.exec(
                    select(HouseholdMember).where(HouseholdMember.active == True)
                ).all()
            )

        resolved_slot = slot or infer_slot(datetime.utcnow().hour)
        member_ids = [m.id for m in members if m.id is not None]

        initial = {
            "slot": resolved_slot.value,
            "session_id": planning_session_id or "",
            "member_ids": member_ids,
        }
        # Empty session_id → graph generates a new UUID in load_context
        if not initial["session_id"]:
            initial.pop("session_id")

        final = await self._graph.ainvoke(
            initial,
            config={"configurable": {"db": self._db, "llm": self._llm}},
        )

        if final.get("error"):
            return {
                "error": final["error"],
                "session_id": final.get("session_id"),
            }

        result: dict = {
            "session_id": final.get("session_id"),
            "pending_questions": final.get("pending_questions") or [],
            "planned_meals": final.get("planned_meals") or [],
            "safety_reports": final.get("safety_reports") or [],
        }
        if final.get("plan_id") is not None:
            result["plan_id"] = final["plan_id"]
        if final.get("message"):
            result["message"] = final["message"]
        return result
