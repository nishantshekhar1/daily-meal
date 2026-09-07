"""Meal planning agent — LangGraph orchestration facade.

Graph topology (see ``planner_graph.py``):

  START → load_context → shortlist ─┬─→ rank_by_cuisine → allocate ─┬─→ write_recipes → END
                                    │                               └─→ ask_clarifications → END
                                    └─→ END (empty shortlist)

LLM proposes dishes/recipes; Python owns cuisine ranking, inventory math and
toddler safety.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Optional

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

    # ── Internals shared by the blocking and streaming entry points ─────────

    def _initial_state(
        self,
        slot: Optional[MealSlot],
        members: Optional[list[HouseholdMember]],
        planning_session_id: Optional[str],
    ) -> dict[str, Any]:
        if members is None:
            members = list(
                self._db.exec(
                    select(HouseholdMember).where(HouseholdMember.active == True)
                ).all()
            )

        # Local hour, not UTC: "what meal is it" is a wall-clock question and
        # the client infers its default slot the same way.
        resolved_slot = slot or infer_slot(datetime.now().hour)
        initial: dict[str, Any] = {
            "slot": resolved_slot.value,
            "session_id": planning_session_id or "",
            "member_ids": [m.id for m in members if m.id is not None],
        }
        # Empty session_id → graph generates a new UUID in load_context
        if not initial["session_id"]:
            initial.pop("session_id")
        return initial

    @property
    def _run_config(self) -> dict[str, Any]:
        return {"configurable": {"db": self._db, "llm": self._llm}}

    @staticmethod
    def _shape_result(final: dict[str, Any]) -> dict:
        """Map terminal graph state onto the API response shape."""
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

    # ── Entry points ────────────────────────────────────────────────────────

    async def suggest(
        self,
        slot: Optional[MealSlot] = None,
        members: Optional[list[HouseholdMember]] = None,
        planning_session_id: Optional[str] = None,
    ) -> dict:
        """Run the planner graph to completion and return the result dict."""
        final = await self._graph.ainvoke(
            self._initial_state(slot, members, planning_session_id),
            config=self._run_config,
        )
        return self._shape_result(final)

    async def astream_suggest(
        self,
        slot: Optional[MealSlot] = None,
        members: Optional[list[HouseholdMember]] = None,
        planning_session_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Run the planner graph, yielding progress events as they happen.

        Yields the ``status``/``meal``/``safety`` events the nodes write via
        ``_emit``, then a final ``done`` event carrying the same payload
        :meth:`suggest` would have returned.  ``values`` mode is consumed purely
        to capture terminal state; only ``custom`` events reach the client.
        """
        final: dict[str, Any] = {}
        async for mode, chunk in self._graph.astream(
            self._initial_state(slot, members, planning_session_id),
            config=self._run_config,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                yield chunk
            elif mode == "values":
                final = chunk

        yield {"type": "done", "result": self._shape_result(final)}
